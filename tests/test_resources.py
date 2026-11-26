"""Bounded caches and portable pressure handling on quota-limited hosts."""
import asyncio
import errno
from contextlib import asynccontextmanager
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

with patch.dict(os.environ, {
    'TELEGRAM_API_ID':'12345', 'TELEGRAM_API_HASH':'test',
    'TELEGRAM_BOT_TOKEN':'12345:test', 'OWNER_ID':'12345',
    'DATABASE_URL':'mongodb://localhost:27017',
}, clear=True):
    from bot.modules import cache, memory
    from bot.modules.archive_cache import ArchiveCache, ArchiveCapacityError, ARCHIVE_AGE


class MediaQuotaTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.patches=patch.multiple(cache,CACHE_DIR=self.directory.name,MAX_CACHE_BYTES=8,
            _INITIALIZED=False,_TOTAL_CACHE_SIZE=0,_CACHE_INDEX=cache.OrderedDict(),
            _CACHE_LOCK=asyncio.Lock(),_IN_FLIGHT_CHUNKS={})
        self.patches.start()
        self.addCleanup(self.patches.stop)

    async def test_admission_evicts_before_write_and_deleted_reads_adjust_usage(self):
        await cache.put_cached_chunk('one',0,b'123456',1)
        await cache.put_cached_chunk('two',0,b'abcd',1)
        self.assertEqual(cache._TOTAL_CACHE_SIZE,4)
        self.assertFalse((Path(self.directory.name)/'one_0.chunk').exists())
        self.assertEqual(await cache.get_cached_chunk('two',0),b'abcd')
        (Path(self.directory.name)/'two_0.chunk').unlink()
        self.assertIsNone(await cache.get_cached_chunk('two',0))
        self.assertEqual(cache._TOTAL_CACHE_SIZE,0)

    async def test_disabled_and_oversized_entries_never_reach_disk(self):
        with patch.object(cache,'MAX_CACHE_BYTES',0):
            await cache.put_cached_chunk('one',0,b'a',1)
            self.assertIsNone(await cache.get_cached_chunk('one',0))
        await cache.put_cached_chunk('one',0,b'a'*9,1)
        self.assertEqual(list(Path(self.directory.name).iterdir()),[])

    async def test_quota_error_keeps_old_chunk_and_removes_partial(self):
        await cache.put_cached_chunk('one',0,b'old',1)
        with patch.object(cache.os,'replace',side_effect=OSError(errno.EDQUOT,'account quota exceeded')):
            await cache.put_cached_chunk('one',0,b'new',1)
        self.assertEqual(await cache.get_cached_chunk('one',0),b'old')
        self.assertFalse(list(Path(self.directory.name).glob('*.part')))
        self.assertEqual(cache._TOTAL_CACHE_SIZE,3)

    async def test_startup_enforces_reduced_budget_and_removes_own_stale_partials(self):
        root=Path(self.directory.name)
        (root/'one_0.chunk').write_bytes(b'a'*6)
        (root/'two_0.chunk').write_bytes(b'b'*6)
        (root/'.chunk-old.part').write_bytes(b'incomplete')
        cache.init_cache()
        self.assertLessEqual(cache._TOTAL_CACHE_SIZE,8)
        self.assertEqual(sum(p.stat().st_size for p in root.glob('*.chunk')),cache._TOTAL_CACHE_SIZE)
        self.assertFalse((root/'.chunk-old.part').exists())

    async def test_filesystem_free_margin_and_path_traversal_are_respected(self):
        with patch.object(cache.shutil,'disk_usage',return_value=SimpleNamespace(free=0)):
            await cache.put_cached_chunk('one',0,b'ab',1)
        await cache.put_cached_chunk('../escape',0,b'ab',1)
        self.assertEqual(list(Path(self.directory.name).iterdir()),[])


class ZIPQuotaTests(unittest.IsolatedAsyncioTestCase):
    async def test_unavailable_storage_and_zero_budget_have_clear_admission_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            archive=ArchiveCache(directory,0)
            with self.assertRaisesRegex(ArchiveCapacityError,'capacity'):
                await archive.start('a'*43,[],None,time.time()+ARCHIVE_AGE)
            archive.max_bytes=1024
            with patch.object(archive,'_prune',side_effect=OSError(errno.EDQUOT,'quota')):
                with self.assertRaisesRegex(ArchiveCapacityError,'quota'):
                    await archive.start('b'*43,[],None,time.time()+ARCHIVE_AGE)
            self.assertEqual(archive.jobs,{})

    async def test_mid_build_quota_error_never_publishes_ready_zip(self):
        for number in (errno.EDQUOT,errno.ENOSPC):
            with self.subTest(errno=number),tempfile.TemporaryDirectory() as directory:
                archive=ArchiveCache(directory,1024)
                write=AsyncMock(side_effect=OSError(number,'quota or disk full'))
                @asynccontextmanager
                async def disk_full(path,*args):
                    Path(path).touch()
                    yield SimpleNamespace(write=write,flush=AsyncMock())
                with patch('bot.modules.archive_cache.aiofiles.open',disk_full):
                    await archive.start('a'*43,[],None,time.time()+ARCHIVE_AGE,progressive=True)
                    await archive.jobs['a'*43]['task']
                write.assert_awaited_once()
                self.assertEqual(archive.status('a'*43,22)['state'],'failed')
                self.assertEqual(list(Path(directory).iterdir()),[])


class PortableMemoryTests(unittest.TestCase):
    def test_freebsd_allocator_uses_only_supported_purge_interface(self):
        library=Mock()
        with patch.object(memory.sys,'platform','freebsd14'),patch.object(memory.ctypes,'CDLL',return_value=library), \
             patch.object(memory.gc,'collect'):
            memory.flush_ram()
        names=[call.args[0] for call in library.mallctl.call_args_list]
        self.assertEqual(names,[b'thread.tcache.flush',b'arena.4096.purge'])
        library.malloc_trim.assert_not_called()

    def test_resource_rss_units_match_freebsd_and_macos(self):
        import psutil
        import resource
        for platform,rss in [('freebsd14',128*1024),('linux',128*1024),('darwin',128*1024*1024)]:
            with self.subTest(platform=platform),patch.object(memory.sys,'platform',platform), \
                 patch.object(psutil,'Process',side_effect=OSError), \
                 patch.object(resource,'getrusage',return_value=SimpleNamespace(ru_maxrss=rss)):
                self.assertEqual(memory.get_current_ram_mb(),128)

    def test_missing_allocator_symbols_are_harmless(self):
        with patch.object(memory.sys,'platform','freebsd14'),patch.object(memory.ctypes,'CDLL',return_value=object()), \
             patch.object(memory.gc,'collect') as collect:
            memory.flush_ram()
        collect.assert_called_once()
