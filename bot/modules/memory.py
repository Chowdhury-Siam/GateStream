"""Portable memory accounting and cooperative pressure recovery."""
import asyncio
import ctypes
import gc
import os
import sys
from logging import getLogger
from bot.config import Resources
from bot.modules.lifecycle import request_restart

logger = getLogger('memory')
gc.enable()
gc.set_threshold(400, 5, 5)


def flush_ram():
    gc.collect()
    try:
        libc = ctypes.CDLL(None)
        if sys.platform.startswith('linux') and hasattr(libc, 'malloc_trim'):
            libc.malloc_trim.argtypes = [ctypes.c_size_t]
            libc.malloc_trim.restype = ctypes.c_int
            libc.malloc_trim(0)
        elif sys.platform.startswith(('freebsd', 'dragonfly')) and hasattr(libc, 'mallctl'):
            libc.mallctl.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p,
                                    ctypes.c_void_p, ctypes.c_size_t]
            libc.mallctl.restype = ctypes.c_int
            libc.mallctl(b'thread.tcache.flush', None, None, None, 0)
            libc.mallctl(b'arena.4096.purge', None, None, None, 0)
    except (OSError, AttributeError):
        pass  # Allocator hooks are optional; garbage collection works everywhere.


def get_current_ram_mb():
    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    except Exception:
        pass
    try:
        import resource
        divisor = 1024 * 1024 if sys.platform == 'darwin' else 1024
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / divisor
    except (ImportError, OSError, AttributeError):
        return 0.0


def check_memory_circuit_breaker(clean_threshold_mb=None, restart_threshold_mb=None):
    clean = Resources.MEMORY_CLEAN_MB if clean_threshold_mb is None else clean_threshold_mb
    restart = Resources.MEMORY_RESTART_MB if restart_threshold_mb is None else restart_threshold_mb
    rss = get_current_ram_mb()
    if rss <= 0 or rss < clean:
        return False
    from bot.modules.telegram import clear_message_caches
    clear_message_caches()
    flush_ram()
    current = get_current_ram_mb()
    logger.warning('Memory cleanup: %.1f MiB -> %.1f MiB.', rss, current)
    if restart and current >= restart:
        if request_restart():
            logger.warning('Memory limit reached; requesting a graceful restart.')
    return True


async def monitor_memory(stop):
    """Watch process memory independently of transfer volume and worker health."""
    while not stop.is_set():
        check_memory_circuit_breaker()
        try:
            async with asyncio.timeout(5):
                await stop.wait()
        except TimeoutError:
            pass
