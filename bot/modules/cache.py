import os
import asyncio
import re
import tempfile
import shutil
from bot.config import Resources
from collections import OrderedDict
from logging import getLogger

logger = getLogger('cache')

CACHE_DIR = str(Resources.MEDIA_CACHE_DIR)
MAX_CACHE_BYTES = Resources.MEDIA_CACHE_MAX_MB * 1024 * 1024

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
    return MAX_CACHE_BYTES > 0 and (file_code, chunk_idx) in _CACHE_INDEX

async def prefetch_tail_chunk(file_code: str, total_chunks: int, channel_id, message_id):
    """Use at most one speculative Telegram read for playback seeking."""
    if MAX_CACHE_BYTES == 0 or total_chunks <= 4:
        return

    chunk_idx = total_chunks - 1
    key = (file_code, chunk_idx)
    async with _CACHE_LOCK:
        if _IN_FLIGHT_CHUNKS or is_chunk_cached(file_code, chunk_idx):
            return
        ready = asyncio.Event()
        _IN_FLIGHT_CHUNKS[key] = ready

    try:
        from bot.clients import get_worker_client, TelegramBot
        from bot.modules.telegram import get_message, get_media_chunk
        worker = get_worker_client() or TelegramBot
        msg = await get_message(channel_id, message_id, client=worker, allow_fallback=False)
        if not msg and worker != TelegramBot:
            worker = TelegramBot
            msg = await get_message(channel_id, message_id, client=worker, allow_fallback=False)
        if msg:
            chunk = await get_media_chunk(worker, msg, chunk_idx)
            if chunk:
                await put_cached_chunk(file_code, chunk_idx, chunk, total_chunks)
    except Exception as error:
        logger.debug('Playback tail prefetch ended (%s).', type(error).__name__)
    finally:
        _IN_FLIGHT_CHUNKS.pop(key, None)
        ready.set()


def init_cache():
    """Initializes the local SSD chunk cache directory and scans existing chunks into LRU index."""
    global _INITIALIZED, _TOTAL_CACHE_SIZE
    if _INITIALIZED:
        return

    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        total = 0
        for fname in os.listdir(CACHE_DIR):
            if fname.startswith('.chunk-') and fname.endswith('.part'):
                os.unlink(os.path.join(CACHE_DIR, fname))
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
        while _TOTAL_CACHE_SIZE > MAX_CACHE_BYTES and _CACHE_INDEX:
            key, size = next(iter(_CACHE_INDEX.items()))
            os.remove(os.path.join(CACHE_DIR, f'{key[0]}_{key[1]}.chunk'))
            _CACHE_INDEX.pop(key)
            _TOTAL_CACHE_SIZE -= size
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
    if MAX_CACHE_BYTES == 0 or not re.fullmatch(r"[A-Za-z0-9_-]+", file_code):
        return None
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
    global _TOTAL_CACHE_SIZE
    async with _CACHE_LOCK:
        if key in _CACHE_INDEX:
            _TOTAL_CACHE_SIZE -= _CACHE_INDEX.pop(key, 0)
    return None

async def put_cached_chunk(file_code: str, chunk_idx: int, data: bytes, total_chunks: int):
    """Bound admission before atomic writes; quota failures never publish partial chunks."""
    global _TOTAL_CACHE_SIZE
    if (not data or len(data) > MAX_CACHE_BYTES or not is_head_or_tail(chunk_idx, total_chunks)
            or not re.fullmatch(r'[A-Za-z0-9_-]+', file_code)):
        return
    if not _INITIALIZED:
        init_cache()
    key = (file_code, chunk_idx)
    path = os.path.join(CACHE_DIR, f'{file_code}_{chunk_idx}.chunk')
    async with _CACHE_LOCK:
        try:
            # Perform the short bounded disk operation under this lock. Running it in
            # a detached thread would allow cancellation to publish an unindexed file.
            old = _CACHE_INDEX.get(key, 0)
            while _TOTAL_CACHE_SIZE - old + len(data) > MAX_CACHE_BYTES:
                victim = next((k for k in _CACHE_INDEX if k != key), None)
                if victim is None:
                    return
                os.remove(os.path.join(CACHE_DIR, f'{victim[0]}_{victim[1]}.chunk'))
                _TOTAL_CACHE_SIZE -= _CACHE_INDEX.pop(victim)
            if shutil.disk_usage(CACHE_DIR).free < len(data) + Resources.CACHE_FREE_MB * 1024 * 1024:
                logger.warning('Media cache write skipped: insufficient free disk space.')
                return
            temporary = None
            try:
                fd, temporary = tempfile.mkstemp(prefix='.chunk-', suffix='.part', dir=CACHE_DIR)
                with os.fdopen(fd, 'wb') as output:
                    output.write(data)
                os.replace(temporary, path)
            finally:
                if temporary and os.path.exists(temporary):
                    os.unlink(temporary)
            _CACHE_INDEX.pop(key, None)
            _CACHE_INDEX[key] = len(data)
            _TOTAL_CACHE_SIZE += len(data) - old
        except OSError as error:
            logger.warning('Media cache write skipped (%s); check disk quota and permissions.', type(error).__name__)
