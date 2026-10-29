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
    def fixture(self, block=False):
        chunk_size = 1024 * 1024
        chunks = [bytes([index]) * chunk_size for index in range(5)] + [b'last seventeen!!!']
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
            is_head_or_tail=lambda *args:False,add_bandwidth_bytes=AsyncMock(),
            flush_ram=lambda:None,check_memory_circuit_breaker=lambda:None,
            activity=TransferActivity(),
        ).items():
            stack.enter_context(patch.object(main,name,value))
        return stack,state,chunks

    async def test_zip_source_fetches_concurrently_with_one_bot_and_keeps_byte_order(self):
        stack,state,chunks=self.fixture()
        with stack:
            async with instance.test_request_context('/index/archive/ticket/GateStream.zip'):
                response=await main.transmit_file('code',range_allowed=False,parallel=True)
                data=await response.get_data()
        self.assertEqual(data,b''.join(chunks))
        self.assertGreater(state['peak'],1)
        self.assertLessEqual(state['peak'],4)
        self.assertEqual(sorted(state['calls']),[(i,1) for i in range(len(chunks))])
        self.assertEqual(state['active'],0)

    async def test_individual_download_keeps_continuous_stream_for_manager_ranges(self):
        stack,state,chunks=self.fixture()
        with stack:
            async with instance.test_request_context('/dl/code'):
                response=await main.transmit_file('code')
                data=await response.get_data()
        self.assertEqual(data,b''.join(chunks))
        self.assertEqual(state['peak'],1)
        self.assertEqual(state['calls'],[(0,0)])

    async def test_disconnect_cancels_and_drains_pending_chunk_reads(self):
        stack,state,chunks=self.fixture(block=True)
        with stack:
            async with instance.test_request_context('/index/archive/ticket/GateStream.zip'):
                response=await main.transmit_file('code',range_allowed=False,parallel=True)
                async with response.response as body:
                    self.assertEqual(await anext(body.__aiter__()),chunks[0])
        self.assertEqual(state['active'],0)
        self.assertGreater(state['cancelled'],0)
