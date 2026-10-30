import os
import asyncio
from collections import OrderedDict
from logging import getLogger
from math import ceil

logger = getLogger('cache')

# Cache storage directory on local VPS SSD
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE_DIR = os.path.join(REPO_ROOT, "cache", "media")

# Maximum disk cache size: 500 MB (stores Head 4MB + Tail 2MB for ~80 active videos)
MAX_CACHE_BYTES = 500 * 1024 * 1024

_CACHE_INDEX: OrderedDict[tuple[str, int], int] = OrderedDict()
_TOTAL_CACHE_SIZE = 0
_CACHE_LOCK = asyncio.Lock()
_INITIALIZED = False

def is_head_or_tail(chunk_idx: int, total_chunks: int) -> bool:
    """
    Returns True if chunk_idx belongs to the head (first 4 MB: EBML header/codecs/opening buffer)
    or tail (last 2 MB: Matroska Cues seek index / MP4 moov atom).
    Only these critical seek chunks are cached to protect disk space.
    """
    # First 4 MB (chunks 0, 1, 2, 3) gives an instant 20-30s opening video buffer
    if chunk_idx < 4:
        return True
    # Last 2 MB covers all Matroska Cues and MP4 index atoms
    if total_chunks > 0 and chunk_idx >= max(0, total_chunks - 2):
        return True
    return False

_IN_FLIGHT_CHUNKS: dict[tuple[str, int], asyncio.Event] = {}

def is_chunk_cached(file_code: str, chunk_idx: int) -> bool:
    """Fast in-memory check whether a chunk is already cached on local SSD."""
    return (file_code, chunk_idx) in _CACHE_INDEX

async def prefetch_chunk(file_code: str, chunk_idx: int, total_chunks: int, target_msg=None, channel_id: int | str = None, message_id: int = None):
    """
    Proactively fetches and caches a critical chunk (Head 0 or Tail EOF) in a detached background task.
    Uses in-flight locking so concurrent requests can wait for completion instead of duplicate downloading.
    """
    if is_chunk_cached(file_code, chunk_idx):
        return

    key = (file_code, chunk_idx)
    async with _CACHE_LOCK:
        if key in _IN_FLIGHT_CHUNKS:
            return  # Already being fetched by another worker task
        evt = asyncio.Event()
        _IN_FLIGHT_CHUNKS[key] = evt

    try:
        from bot.clients import get_worker_client, TelegramBot
        from bot.modules.telegram import get_message, get_media_chunk
        worker = get_worker_client() or TelegramBot
        msg = target_msg
        if channel_id and message_id:
            msg = await get_message(channel_id, message_id, client=worker, allow_fallback=False)
            if not msg and worker != TelegramBot:
                worker = TelegramBot
                msg = await get_message(channel_id, message_id, client=worker, allow_fallback=False)

        if not msg:
            return

        chunk = await get_media_chunk(worker, msg, chunk_idx)
        if chunk:
            await put_cached_chunk(file_code, chunk_idx, chunk, total_chunks)
            logger.info("Detached prefetch saved chunk %d for %s (size %d bytes).", chunk_idx, file_code, len(chunk))
    except Exception as e:
        logger.debug("Prefetch background task exception for chunk %d: %s", chunk_idx, e)
    finally:
        async with _CACHE_LOCK:
            evt.set()
            _IN_FLIGHT_CHUNKS.pop(key, None)

async def prefetch_head_chunk(file_code: str, total_chunks: int, target_msg=None, channel_id: int | str = None, message_id: int = None):
    """Detached un-cancellable prefetcher for chunk 0."""
    await prefetch_chunk(file_code, 0, total_chunks, target_msg, channel_id, message_id)

async def prefetch_tail_chunk(file_code: str, total_chunks: int, target_msg=None, channel_id: int | str = None, message_id: int = None):
    """Detached un-cancellable prefetcher for EOF tail chunk (Matroska Cues)."""
    if total_chunks <= 4:
        return
    tail_idx = total_chunks - 1
    await prefetch_chunk(file_code, tail_idx, total_chunks, target_msg, channel_id, message_id)

def init_cache():
    """Initializes the local SSD chunk cache directory and scans existing chunks into LRU index."""
    global _INITIALIZED, _TOTAL_CACHE_SIZE
    if _INITIALIZED:
        return

    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        total = 0
        for fname in os.listdir(CACHE_DIR):
            if fname.endswith(".chunk"):
                fpath = os.path.join(CACHE_DIR, fname)
                try:
                    fsize = os.path.getsize(fpath)
                    # Expected format: {file_code}_{chunk_idx}.chunk
                    parts = fname[:-6].rsplit('_', 1)
                    if len(parts) == 2 and parts[1].isdigit():
                        code = parts[0]
                        c_idx = int(parts[1])
                        _CACHE_INDEX[(code, c_idx)] = fsize
                        total += fsize
                except Exception:
                    pass
        _TOTAL_CACHE_SIZE = total
        _INITIALIZED = True
        logger.info("Initialized Head & Tail disk cache: %d chunk(s) loaded (%.2f MB total).", len(_CACHE_INDEX), total / (1024 * 1024))
    except Exception as e:
        logger.warning("Failed to initialize chunk cache directory: %s", e)

async def get_cached_chunk(file_code: str, chunk_idx: int, wait_in_flight: bool = True) -> bytes | None:
    """
    Retrieves a cached chunk from local SSD in ~0.5ms.
    If chunk is currently being prefetched in background and wait_in_flight is True,
    waits for the in-flight task to complete instead of immediately returning None.
    Returns bytes if present, or None if not cached.
    """
    if not _INITIALIZED:
        init_cache()

    key = (file_code, chunk_idx)
    async with _CACHE_LOCK:
        has_chunk = key in _CACHE_INDEX
        in_flight_evt = _IN_FLIGHT_CHUNKS.get(key) if (not has_chunk and wait_in_flight) else None

    # If chunk is currently being downloaded in background, await its completion!
    if in_flight_evt is not None:
        try:
            await asyncio.wait_for(in_flight_evt.wait(), timeout=4.0)
        except (asyncio.TimeoutError, Exception):
            pass
        async with _CACHE_LOCK:
            has_chunk = key in _CACHE_INDEX

    if not has_chunk:
        return None

    async with _CACHE_LOCK:
        if key in _CACHE_INDEX:
            _CACHE_INDEX.move_to_end(key)
        else:
            return None

    filename = f"{file_code}_{chunk_idx}.chunk"
    path = os.path.join(CACHE_DIR, filename)

    try:
        def _read():
            if os.path.exists(path):
                with open(path, 'rb') as f:
                    return f.read()
            return None

        data = await asyncio.to_thread(_read)
        if data is not None and len(data) > 0:
            return data
    except Exception as e:
        logger.warning("Error reading cached chunk %s_%d: %s", file_code, chunk_idx, e)

    # If file was missing or corrupt, evict key from index
    async with _CACHE_LOCK:
        if key in _CACHE_INDEX:
            _CACHE_INDEX.pop(key, None)
    return None

async def put_cached_chunk(file_code: str, chunk_idx: int, data: bytes, total_chunks: int):
    """
    Persists a critical head/tail chunk to local SSD with automatic LRU eviction.
    """
    if not _INITIALIZED:
        init_cache()

    if not is_head_or_tail(chunk_idx, total_chunks):
        return
    if not data:
        return

    key = (file_code, chunk_idx)
    chunk_len = len(data)
    filename = f"{file_code}_{chunk_idx}.chunk"
    path = os.path.join(CACHE_DIR, filename)

    try:
        def _write():
            with open(path, 'wb') as f:
                f.write(data)

        await asyncio.to_thread(_write)

        global _TOTAL_CACHE_SIZE
        async with _CACHE_LOCK:
            old_size = _CACHE_INDEX.pop(key, 0)
            _TOTAL_CACHE_SIZE -= old_size

            _CACHE_INDEX[key] = chunk_len
            _TOTAL_CACHE_SIZE += chunk_len

            # Evict oldest chunks if exceeding MAX_CACHE_BYTES
            while _TOTAL_CACHE_SIZE > MAX_CACHE_BYTES and _CACHE_INDEX:
                evict_key, evict_size = _CACHE_INDEX.popitem(last=False)
                _TOTAL_CACHE_SIZE -= evict_size
                evict_file = os.path.join(CACHE_DIR, f"{evict_key[0]}_{evict_key[1]}.chunk")
                try:
                    if os.path.exists(evict_file):
                        os.remove(evict_file)
                except Exception:
                    pass
    except Exception as e:
        logger.warning("Error saving cached chunk %s_%d: %s", file_code, chunk_idx, e)

def evict_file_chunks(file_code: str):
    """Removes any cached SSD chunks for a specific file code so replaced media streams fresh."""
    global _TOTAL_CACHE_SIZE
    if not file_code:
        return
    try:
        keys_to_remove = [k for k in _CACHE_INDEX.keys() if k[0] == file_code]
        for key in keys_to_remove:
            size = _CACHE_INDEX.pop(key, 0)
            _TOTAL_CACHE_SIZE -= size
            fpath = os.path.join(CACHE_DIR, f"{key[0]}_{key[1]}.chunk")
            try:
                if os.path.exists(fpath):
                    os.remove(fpath)
            except Exception:
                pass
        if keys_to_remove:
            logger.info("Evicted %d cached chunk(s) for file %s.", len(keys_to_remove), file_code)
    except Exception as e:
        logger.warning("Error evicting chunks for %s: %s", file_code, e)

def clear_media_cache():
    """Removes all cached chunks from disk to free space immediately."""
    global _TOTAL_CACHE_SIZE
    try:
        if os.path.exists(CACHE_DIR):
            for fname in os.listdir(CACHE_DIR):
                if fname.endswith(".chunk"):
                    try:
                        os.remove(os.path.join(CACHE_DIR, fname))
                    except Exception:
                        pass
        _CACHE_INDEX.clear()
        _TOTAL_CACHE_SIZE = 0
        logger.info("Cleared all media cache chunks from disk.")
    except Exception as e:
        logger.warning("Error clearing media cache: %s", e)
