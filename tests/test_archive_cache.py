import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from quart import Response

with patch.dict(os.environ, {
    'TELEGRAM_API_ID':'12345', 'TELEGRAM_API_HASH':'test',
    'TELEGRAM_BOT_TOKEN':'12345:test', 'OWNER_ID':'12345',
    'DATABASE_URL':'mongodb://localhost:27017',
}, clear=True):
    from bot.modules.archive_cache import ArchiveCache, ARCHIVE_AGE
    from bot.modules.archive import archive_size


class ArchiveCacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.cache = ArchiveCache(self.directory.name, 1024 * 1024)
        self.addCleanup(self.directory.cleanup)
        self.addAsyncCleanup(self.cache.close)
        self.files = [dict(code='one', file_name='test.bin', file_size=4)]
        self.expiry = time.time() + ARCHIVE_AGE
        self.calls = []

    async def download(self, code):
        self.calls.append(code)
        async def body():
            yield b'abcd'
        return Response(body(), headers={'Content-Length':'4'})

    async def finish(self, token):
        await asyncio.wait_for(self.cache.jobs[token]['task'], 1)
        return self.cache.status(token, archive_size(self.files))

    async def test_one_build_survives_process_restart_without_regenerating_zip(self):
        token = 'a' * 43
        await self.cache.start(token, self.files, self.download, self.expiry)
        await self.cache.start(token, self.files, self.download, self.expiry)
        self.assertEqual((await self.finish(token))['state'], 'ready')
        before = self.cache.path(token).read_bytes()
        restarted = ArchiveCache(self.directory.name, self.cache.max_bytes)
        self.assertEqual(restarted.status(token, len(before))['state'], 'ready')
        self.assertEqual(self.calls, ['one'])
        self.assertEqual(restarted.path(token).read_bytes(), before)
        # A lost ZIP must never be silently regenerated underneath a partial download.
        restarted.path(token).unlink()
        self.assertEqual(self.cache.status(token, len(before))['state'], 'missing')

    async def test_capacity_preserves_unexpired_resume_files_and_prunes_expired_ones(self):
        total = archive_size(self.files)
        self.cache.max_bytes = total
        await self.cache.start('a'*43, self.files, self.download, self.expiry)
        await self.finish('a'*43)
        with self.assertRaisesRegex(ValueError, 'capacity'):
            await self.cache.start('b'*43, self.files, self.download, self.expiry)
        path = self.cache.path('a'*43)
        stamp = time.time() - ARCHIVE_AGE - 1
        os.utime(path, (stamp, stamp))
        await self.cache.start('b'*43, self.files, self.download, self.expiry)
        self.assertFalse(path.exists())
        self.assertEqual((await self.finish('b'*43))['state'], 'ready')

    async def test_failure_and_shutdown_remove_incomplete_zip_files(self):
        async def truncated(code):
            async def body(): yield b'ab'
            return Response(body(), headers={'Content-Length':'4'})
        await self.cache.start('a'*43, self.files, truncated, self.expiry)
        self.assertEqual((await self.finish('a'*43))['state'], 'failed')
        entered = asyncio.Event()
        async def blocked(code):
            async def body():
                entered.set()
                await asyncio.Event().wait()
                yield b'abcd'
            return Response(body(), headers={'Content-Length':'4'})
        await self.cache.start('b'*43, self.files, blocked, self.expiry)
        await asyncio.wait_for(entered.wait(), 1)
        await self.cache.close()
        self.assertFalse(list(Path(self.directory.name).glob('*.part')))
        self.assertFalse(list(Path(self.directory.name).glob('*.zip')))

    async def test_changed_source_size_cannot_exceed_reserved_space(self):
        async def changed(code):
            async def body(): yield b'a' * 100
            return Response(body(), headers={'Content-Length':'100'})
        await self.cache.start('a'*43, self.files, changed, self.expiry)
        self.assertEqual((await self.finish('a'*43))['state'], 'failed')
        self.assertFalse(self.cache.path('a'*43).exists())

    async def test_active_preparation_reserves_space_and_is_not_downloadable(self):
        self.cache.max_bytes = archive_size(self.files)
        entered = asyncio.Event()
        release = asyncio.Event()
        async def blocked(code):
            entered.set()
            await release.wait()
            return await self.download(code)
        await self.cache.start('a'*43, self.files, blocked, self.expiry)
        await asyncio.wait_for(entered.wait(), 1)
        self.assertEqual(self.cache.status('a'*43, archive_size(self.files))['state'], 'preparing')
        self.assertFalse(self.cache.path('a'*43).exists())
        with self.assertRaisesRegex(ValueError, 'capacity'):
            await self.cache.start('b'*43, self.files, self.download, self.expiry)
        release.set()
        self.assertEqual((await self.finish('a'*43))['state'], 'ready')
