import gc
import sys
import ctypes

# Optimize Python GC threshold for low memory / high throughput environments
gc.enable()
gc.set_threshold(400, 5, 5)

def flush_ram():
    """
    Forces Python cyclic garbage collection and releases freed heap pages
    back to the OS on both Linux (glibc malloc_trim) and FreeBSD / Serv00 (jemalloc mallctl).
    """
    gc.collect()

    # 1. FreeBSD / Serv00: jemalloc is built into FreeBSD's libc (libc.so.7)
    if sys.platform.startswith(('freebsd', 'dragonfly')):
        for libname in ('libc.so.7', 'libc.so', None):
            try:
                libc = ctypes.CDLL(libname)
                if hasattr(libc, 'mallctl'):
                    libc.mallctl.argtypes = [
                        ctypes.c_char_p,
                        ctypes.c_void_p,
                        ctypes.c_void_p,
                        ctypes.c_void_p,
                        ctypes.c_size_t
                    ]
                    libc.mallctl.restype = ctypes.c_int
                    # Flush thread local caches
                    libc.mallctl(b"thread.tcache.flush", None, None, None, 0)
                    # Purge unused dirty memory pages back to FreeBSD kernel
                    libc.mallctl(b"arena.4096.purge", None, None, None, 0)
                    libc.mallctl(b"arenas.purge", None, None, None, 0)
                    break
            except Exception:
                continue

    # 2. Linux (Alwaysdata, Ubuntu, Debian): glibc malloc_trim
    elif sys.platform.startswith('linux'):
        for libname in ('libc.so.6', 'libc.so'):
            try:
                libc = ctypes.CDLL(libname)
                if hasattr(libc, 'malloc_trim'):
                    libc.malloc_trim(0)
                    break
            except Exception:
                continue

    # 3. Fallback generic check (if jemalloc was dynamically linked)
    try:
        libc = ctypes.CDLL(None)
        if hasattr(libc, 'mallctl'):
            libc.mallctl.argtypes = [
                ctypes.c_char_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_size_t
            ]
            libc.mallctl.restype = ctypes.c_int
            libc.mallctl(b"thread.tcache.flush", None, None, None, 0)
            libc.mallctl(b"arenas.purge", None, None, None, 0)
    except Exception:
        pass

import os
from logging import getLogger

logger = getLogger('memory')

def get_current_ram_mb() -> float:
    """Returns current process Resident Set Size (RSS) in megabytes."""
    # 1. Try psutil if installed
    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    except Exception:
        pass

    # 2. Standard library resource module (built-in on FreeBSD / Linux / macOS)
    try:
        import resource
        # ru_maxrss is in kilobytes on Linux and FreeBSD
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    except Exception:
        pass

    # 3. Linux /proc/self/status VmRSS fallback
    try:
        with open('/proc/self/status', 'r') as f:
            for line in f:
                if line.startswith('VmRSS:'):
                    return float(line.split()[1]) / 1024.0
    except Exception:
        pass

    return 0.0

def check_memory_circuit_breaker(clean_threshold_mb: int = 420, restart_threshold_mb: int = 498, threshold_mb: int = None) -> bool:
    """
    Strict 500 MB Memory Guardian for Serv00 (512 MB host limit):
    1. At clean_threshold_mb (default 420 MB): Proactively runs cyclic GC,
       jemalloc arena purging back to FreeBSD kernel, and shrinks _MESSAGE_CACHE.
    2. At restart_threshold_mb (default 498 MB): If heap fragmentation prevents
       releasing memory and RAM reaches the critical ceiling, triggers an instant,
       seamless in-place self-restart (os.execl) in ~1.5s to reset heap before Serv00's
       host kernel can issue an abrupt SIGKILL.
    """
    if threshold_mb is not None:
        clean_threshold_mb = threshold_mb

    rss_mb = get_current_ram_mb()
    if rss_mb <= 0:
        return False

    if rss_mb >= clean_threshold_mb:
        # Step 1: In-process cleanup (no restart needed)
        flush_ram()
        try:
            from bot.modules.telegram import _MESSAGE_CACHE
            if len(_MESSAGE_CACHE) > 20:
                while len(_MESSAGE_CACHE) > 20:
                    _MESSAGE_CACHE.popitem(last=False)
        except Exception:
            pass
        flush_ram()

        new_rss = get_current_ram_mb()
        logger.warning(
            "Memory Circuit Breaker: RSS was %.1f MB -> compacted to %.1f MB",
            rss_mb, new_rss
        )

        # Step 2: Fail-safe self-reboot guard if still at or above critical ceiling
        if new_rss >= restart_threshold_mb:
            logger.critical(
                "🚨 CRITICAL: Process RAM at %.1f MB (Ceiling: %d MB)! "
                "Performing seamless 1.5s self-reboot to clear fragmented heap and prevent Serv00 SIGKILL...",
                new_rss, restart_threshold_mb
            )
            try:
                if os.path.exists("start.sh"):
                    os.utime("start.sh", None)
            except Exception:
                pass
            try:
                os.execl(sys.executable, sys.executable, "-m", "bot")
            except Exception as e:
                logger.error("Failed to execute self-reboot: %s", e)
                sys.exit(1)

        return True

    return False
