"""Bounded caches and portable pressure handling on quota-limited hosts."""
import asyncio
import errno
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

with patch.dict(os.environ, {
    'TELEGRAM_API_ID':'12345', 'TELEGRAM_API_HASH':'test',
    'TELEGRAM_BOT_TOKEN':'12345:test', 'OWNER_ID':'12345',
    'DATABASE_URL':'mongodb://localhost:27017',
}, clear=True):
    from bot.modules import cache, memory


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


    async def test_slow_tail_prefetch_is_bounded_and_cancellation_releases_its_slot(self):
        from bot import clients
        from bot.modules import telegram

        entered = asyncio.Event()

        async def read(*args):
            entered.set()
            await asyncio.Event().wait()

        with patch.object(clients, 'get_worker_client', return_value=clients.TelegramBot), \
             patch.object(telegram, 'get_message', AsyncMock(return_value=object())), \
             patch.object(telegram, 'get_media_chunk', AsyncMock(side_effect=read)) as download:
            first = asyncio.create_task(cache.prefetch_tail_chunk('one', 10, 1, 2))
            try:
                await asyncio.wait_for(entered.wait(), timeout=1)
                await asyncio.wait_for(cache.prefetch_tail_chunk('two', 10, 1, 3), timeout=1)
                download.assert_awaited_once()
            finally:
                first.cancel()
                await asyncio.gather(first, return_exceptions=True)
            self.assertFalse(cache._IN_FLIGHT_CHUNKS)

            download.side_effect = None
            download.return_value = b'tail'
            await cache.prefetch_tail_chunk('two', 10, 1, 3)
            self.assertEqual(await cache.get_cached_chunk('two', 9), b'tail')


    async def test_revoked_chunks_and_manual_cleanup_update_the_disk_cache(self):
        directory = Path(self.directory.name)
        unrelated = directory / 'keep.txt'
        unrelated.write_text('keep')
        await cache.put_cached_chunk('replaced', 0, b'old', 1)
        await cache.put_cached_chunk('other', 0, b'new', 1)

        cache.evict_file_chunks('replaced')
        self.assertIsNone(await cache.get_cached_chunk('replaced', 0))
        self.assertEqual(cache._TOTAL_CACHE_SIZE, 3)
        self.assertEqual(cache.clear_media_cache(), 1)
        self.assertEqual(cache._TOTAL_CACHE_SIZE, 0)
        self.assertFalse(cache._CACHE_INDEX)
        self.assertEqual(list(directory.iterdir()), [unrelated])


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


class MemoryMonitorTests(unittest.IsolatedAsyncioTestCase):
    async def test_monitor_checks_idle_process_and_stops_promptly(self):
        stop = asyncio.Event()
        with patch.object(memory, 'get_current_ram_mb', side_effect=lambda: self.sample(stop)) as rss, \
             patch.object(memory, 'flush_ram') as collect:
            await asyncio.wait_for(memory.monitor_memory(stop), timeout=1)
        rss.assert_called_once()
        collect.assert_not_called()

    @staticmethod
    def sample(stop):
        stop.set()
        return 200
