import asyncio
import os
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

with patch.dict(os.environ, {
    'TELEGRAM_API_ID':'12345','TELEGRAM_API_HASH':'test_hash','TELEGRAM_BOT_TOKEN':'12345:test_token',
    'TELEGRAM_BOT_USERNAME':'TestGateStreamBot','OWNER_ID':'12345','DATABASE_URL':'mongodb://localhost:27017',
    'SPACE_ID':'user/app','SPACE_HOST':'user-app.hf.space',
}, clear=True):
    from bot.server import instance, main
    from bot.modules.activity import TransferActivity


class BulkPipelineTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self, block=False, chunk_count=6):
        chunk_size = 1024 * 1024
        chunks = [bytes([index % 256]) * chunk_size for index in range(chunk_count-1)] + [b'last seventeen!!!']
        state = dict(active=0, peak=0, cancelled=0, calls=[])

        async def stream_media(message, offset=0, limit=0):
            state['calls'].append((offset, limit))
            try:
                for index in range(offset, min(len(chunks), offset+limit) if limit else len(chunks)):
                    state['active'] += 1
                    state['peak'] = max(state['peak'], state['active'])
                    try:
                        if block and index:
                            await asyncio.Event().wait()
                        else:
                            # Deliberately finish later offsets first, testing output ordering.
                            await asyncio.sleep(.02 if index == 0 else .005)
                    except asyncio.CancelledError:
                        state['cancelled'] += 1
                        raise
                    finally:
                        state['active'] -= 1
                    yield chunks[index]
            finally:
                pass

        worker = SimpleNamespace(name='only-bot', stream_media=stream_media)
        document = dict(channel_id=1,message_id=2,file_name='test.mkv',
            file_size=sum(map(len,chunks)),mime_type='video/x-matroska')
        stack = ExitStack()
        for name, value in dict(
            get_file=AsyncMock(return_value=document), get_worker_client=lambda:worker,
            worker_clients=[worker],get_message=AsyncMock(return_value=object()),
            get_cached_chunk=AsyncMock(return_value=None),is_chunk_cached=lambda *args:True,
            is_head_or_tail=lambda *args:False,
            activity=TransferActivity(),
        ).items():
            stack.enter_context(patch.object(main,name,value))
        return stack,state,chunks

    async def test_individual_download_keeps_continuous_stream_for_manager_ranges(self):
        stack,state,chunks=self.fixture()
        with stack:
            async with instance.test_request_context('/dl/code'):
                response=await main.transmit_file('code')
                data=await response.get_data()
        self.assertEqual(data,b''.join(chunks))
        self.assertEqual(state['peak'],1)
        self.assertEqual(state['calls'],[(0,len(chunks))])

    async def test_cold_download_uses_foreground_read_and_playback_prefetches_only_tail(self):
        stack, state, chunks = self.fixture()
        tasks = []

        def spawn(coroutine):
            task = asyncio.create_task(coroutine)
            tasks.append(task)
            return task

        with stack, patch.object(main, 'is_chunk_cached', return_value=False), \
             patch.object(main, 'spawn', side_effect=spawn), \
             patch.object(main, 'prefetch_tail_chunk', AsyncMock()) as prefetch:
            async with instance.test_request_context('/dl/code', headers={'Range': 'bytes=0-31'}):
                response = await main.transmit_file('code')
                self.assertEqual(await response.get_data(), chunks[0][:32])
            self.assertEqual(tasks, [])
            self.assertEqual(state['calls'], [(0, 1)])

            async with instance.test_request_context('/dl/code?stream=1', headers={'Range': 'bytes=0-31'}):
                response = await main.transmit_file('code')
                self.assertEqual(await response.get_data(), chunks[0][:32])
            await asyncio.gather(*tasks)
            prefetch.assert_awaited_once_with('code', len(chunks), 1, 2)
            self.assertEqual(len(tasks), 1)

    async def test_continuous_download_recovers_stall_without_repeating_bytes(self):
        from bot.modules import telegram
        stack,state,chunks=self.fixture()
        closed=[]
        with stack:
            worker=main.get_worker_client()
            original=worker.stream_media

            async def stream_media(message, offset=0, limit=0):
                if offset == 0:
                    try:
                        async for chunk in original(message,0,2):
                            yield chunk
                        await asyncio.Event().wait()
                    finally:
                        closed.append(offset)
                else:
                    async for chunk in original(message,offset,limit):
                        yield chunk

            with patch.object(worker,'stream_media',stream_media), \
                 patch.object(telegram,'MEDIA_READ_TIMEOUT',.1), \
                 patch.object(main,'mark_worker_cooldown'):
                async with instance.test_request_context('/dl/code'):
                    response = await main.transmit_file('code')
                    data = await asyncio.wait_for(response.get_data(), 2)
            self.assertEqual(len(data),sum(map(len,chunks)))
            self.assertEqual(data,b''.join(chunks))
            self.assertEqual(state['calls'],[(0,2),(2,len(chunks)-2)])
            self.assertEqual(closed,[0])

    async def test_unaligned_range_includes_last_required_telegram_chunk(self):
        stack,state,chunks=self.fixture()
        start,end=123,2*1024*1024+100
        with stack:
            async with instance.test_request_context('/dl/code',headers={'Range':f'bytes={start}-{end}'}):
                response=await main.transmit_file('code')
                data=await response.get_data()
        self.assertEqual(data,b''.join(chunks)[start:end+1])
        self.assertEqual(state['calls'],[(0,3)])

    async def test_empty_telegram_source_stops_after_bounded_retries(self):
        stack,state,chunks=self.fixture()
        calls=[]

        async def empty_stream(message, offset=0, limit=0):
            calls.append((offset,limit))
            if False:
                yield b''

        with stack, patch.object(main.get_worker_client(),'stream_media',empty_stream), \
             patch.object(main,'mark_worker_cooldown'), \
             patch.object(main.asyncio,'sleep',AsyncMock()):
            async with instance.test_request_context('/dl/code'):
                response = await main.transmit_file('code')
                self.assertEqual(await response.get_data(), b'')
        self.assertEqual(len(calls),12)

    async def test_playback_disconnect_drains_pending_reads(self):
        stack, state, chunks = self.fixture(block=True)
        with stack:
            worker = main.get_worker_client()
            with patch.object(main, 'worker_clients', [worker, object()]):
                async with instance.test_request_context('/dl/code?stream=1'):
                    response = await main.transmit_file('code')
                    async with response.response as body:
                        self.assertEqual(await anext(body.__aiter__()), chunks[0])
        self.assertEqual(state['active'], 0)
        self.assertGreater(state['cancelled'], 0)
