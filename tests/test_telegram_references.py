import asyncio
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

with patch.dict(os.environ, {
    'TELEGRAM_API_ID': '12345', 'TELEGRAM_API_HASH': 'test_hash',
    'TELEGRAM_BOT_TOKEN': '12345:test_token', 'OWNER_ID': '12345',
    'DATABASE_URL': 'mongodb://localhost:27017',
}):
    from bot.modules import telegram


class TelegramReferenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        telegram._MESSAGE_CACHE.clear()
        self.addCleanup(telegram._MESSAGE_CACHE.clear)
        self.old = SimpleNamespace(id=42, chat=SimpleNamespace(id=-100123), empty=False)
        self.fresh = SimpleNamespace(id=42, chat=self.old.chat, empty=False)
        self.worker = SimpleNamespace(name='worker', get_messages=AsyncMock(return_value=self.fresh))

    async def test_sdk_swallowed_reference_error_refreshes_same_worker_and_resumes_after_prefix(self):
        calls, chunks = [], [b'one', b'two', b'three', b'four']
        slots = asyncio.Semaphore(1)
        async def stream_media(message, offset=0, limit=0):
            calls.append((message, offset, limit))
            async with slots:
                for index in range(offset, offset + limit):
                    if message is self.old and index == 2:
                        return  # Hydrogram logs the expired reference, then returns.
                    yield chunks[index]
        self.worker.stream_media = stream_media
        with patch.object(telegram.TelegramBot, 'get_messages', AsyncMock()) as primary:
            result = [data async for data in telegram.iter_media_chunks(self.worker, self.old, 0, 4)]
            primary.assert_not_awaited()
        self.assertEqual(result, chunks)
        self.assertEqual(calls, [(self.old, 0, 4), (self.fresh, 2, 2)])
        self.worker.get_messages.assert_awaited_once_with(chat_id=-100123, message_ids=42)
        self.assertEqual(slots._value, 1)

    async def test_explicit_expired_error_and_single_chunk_reader_use_same_recovery(self):
        from hydrogram.errors import FileReferenceExpired
        async def stream_media(message, offset=0, limit=0):
            self.assertEqual((offset, limit), (17, 1))
            if message is self.old:
                raise FileReferenceExpired
            yield b'chunk'
        self.worker.stream_media = stream_media
        self.assertEqual(await telegram.get_media_chunk(self.worker, self.old, 17), b'chunk')
        self.worker.get_messages.assert_awaited_once()

    async def test_refresh_is_bounded_if_fresh_reference_still_cannot_be_read(self):
        calls = []
        async def empty(message, offset=0, limit=0):
            calls.append(offset)
            if False: yield b''
        self.worker.stream_media = empty
        with self.assertRaisesRegex(IOError, 'before the requested chunks'):
            async for _ in telegram.iter_media_chunks(self.worker, self.old, 8, 2): pass
        self.assertEqual(calls, [8, 8])
        self.worker.get_messages.assert_awaited_once()

    async def test_media_lookup_cannot_return_another_bots_file_reference(self):
        self.worker.get_messages.side_effect = RuntimeError('worker cannot access this channel')
        self.worker.get_chat = AsyncMock(side_effect=RuntimeError('unavailable'))
        with patch.object(telegram.TelegramBot, 'get_messages', AsyncMock(return_value=self.fresh)) as primary:
            self.assertIsNone(await telegram.get_message(-100123, 42, client=self.worker, allow_fallback=False))
            primary.assert_not_awaited()
            self.assertIs(await telegram.get_message(-100123, 42, client=self.worker), self.fresh)
            primary.assert_awaited_once()

    async def test_forced_refresh_only_evicts_the_selected_workers_reference(self):
        other = SimpleNamespace(name='other-worker', get_messages=AsyncMock(return_value=self.old))
        self.assertIs(await telegram.get_message(-100123, 42, client=other), self.old)
        self.assertIs(await telegram.get_message(-100123, 42, client=self.worker, force_refresh=True), self.fresh)
        self.assertIs(await telegram.get_message(-100123, 42, client=other), self.old)
        other.get_messages.assert_awaited_once()

    async def test_cancelled_read_closes_source_without_trying_to_refresh(self):
        closed = []
        async def source(message, offset=0, limit=0):
            try:
                yield b'prefix'
                await asyncio.Event().wait()
            finally:
                closed.append(True)
        self.worker.stream_media = source
        stream = telegram.iter_media_chunks(self.worker, self.old, 0, 2)
        await anext(stream)
        await stream.aclose()
        self.assertEqual(closed, [True])
        self.worker.get_messages.assert_not_awaited()
