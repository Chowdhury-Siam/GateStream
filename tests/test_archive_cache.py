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

    async def test_live_ranges_start_before_completion_and_share_one_valid_zip(self):
        import io
        from zipfile import ZipFile
        entered, release = asyncio.Event(), asyncio.Event()
        async def download(code):
            self.calls.append(code)
            async def body():
                yield b'ab'
                entered.set()
                await release.wait()
                yield b'cd'
            return Response(body(),headers={'Content-Length':'4'})
        token = 'a'*43
        total = archive_size(self.files)
        await self.cache.start(token,self.files,download,self.expiry,progressive=True)
        await asyncio.wait_for(entered.wait(),1)
        async def read(start,stop,cache=None):
            return b''.join([chunk async for chunk in (cache or self.cache).read(token,start,stop)])
        self.assertEqual(await asyncio.wait_for(read(0,2),1),b'PK')
        self.assertFalse(self.cache.jobs[token]['task'].done())
        # Closing a prefix request must leave the shared builder running for others.
        bounds=[(i,min(i+31,total)) for i in range(0,total,31)]
        readers=[asyncio.create_task(read(start,stop)) for start,stop in bounds]
        await asyncio.sleep(.01)
        self.assertFalse(readers[-1].done())
        release.set()
        payload=b''.join(await asyncio.wait_for(asyncio.gather(*readers),1))
        self.assertEqual((await self.finish(token))['state'],'ready')
        self.assertEqual(payload,self.cache.path(token).read_bytes())
        with ZipFile(io.BytesIO(payload)) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(archive.read('01-test.bin'),b'abcd')
        self.assertEqual(self.calls,['one'])
        restarted=ArchiveCache(self.directory.name,self.cache.max_bytes)
        self.assertEqual(await read(13,total,restarted),payload[13:])

    async def test_live_builds_bypass_preparation_queue_and_are_bounded(self):
        entered,release=asyncio.Event(),asyncio.Event()
        async def blocked(code):
            entered.set()
            await release.wait()
            return await self.download(code)
        await self.cache.start('p'*43,self.files,blocked,self.expiry)
        await asyncio.wait_for(entered.wait(),1)
        await self.cache.start('a'*43,self.files,self.download,self.expiry,progressive=True)
        self.assertEqual((await self.finish('a'*43))['state'],'ready')
        self.assertFalse(self.cache.jobs['p'*43]['task'].done())
        await self.cache.start('b'*43,self.files,blocked,self.expiry,progressive=True)
        await self.cache.start('c'*43,self.files,blocked,self.expiry,progressive=True)
        with self.assertRaisesRegex(ValueError,'Live ZIP capacity'):
            await self.cache.start('d'*43,self.files,blocked,self.expiry,progressive=True)
        release.set()
        await asyncio.wait_for(asyncio.gather(*(job['task'] for job in self.cache.jobs.values())),1)

    async def test_failed_live_source_unblocks_waiting_ranges_and_removes_partial(self):
        entered,release=asyncio.Event(),asyncio.Event()
        async def download(code):
            async def body():
                yield b'ab'
                entered.set()
                await release.wait()
                raise OSError('source disconnected')
            return Response(body(),headers={'Content-Length':'4'})
        token='a'*43
        total=archive_size(self.files)
        await self.cache.start(token,self.files,download,self.expiry,progressive=True)
        await asyncio.wait_for(entered.wait(),1)
        async def read_tail():
            return b''.join([chunk async for chunk in self.cache.read(token,total-22,total)])
        reader=asyncio.create_task(read_tail())
        await asyncio.sleep(.01)
        self.assertFalse(reader.done())
        release.set()
        self.assertEqual((await self.finish(token))['state'],'failed')
        with self.assertRaisesRegex(RuntimeError,'preparation stopped'):
            await asyncio.wait_for(reader,1)
        self.assertFalse(list(Path(self.directory.name).glob('*.part')))
        self.assertFalse(self.cache.path(token).exists())

    async def test_live_fill_rate_tracks_progress_and_decays_when_source_stalls(self):
        entered=asyncio.Event()
        async def blocked(code):
            entered.set()
            await asyncio.Event().wait()
        token='a'*43
        await self.cache.start(token,self.files,blocked,self.expiry,progressive=True)
        await asyncio.wait_for(entered.wait(),1)
        job=self.cache.jobs[token]
        job['bytes']=40
        now=time.monotonic()
        job['samples'].clear()
        job['samples'].extend([(now-5,0),(now-1,40)])
        status=self.cache.status(token,archive_size(self.files))
        self.assertAlmostEqual(status['source_speed_bps'],8,delta=.1)
        self.assertEqual(status['bytes'],40)
        job['samples'].clear()
        job['samples'].extend([(now-20,0),(now-10,40)])
        self.assertEqual(self.cache.status(token,archive_size(self.files))['source_speed_bps'],0)
        await self.cache.close()
