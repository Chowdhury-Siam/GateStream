import asyncio
import time
from collections import OrderedDict, deque
from contextlib import aclosing
from hydrogram import Client
from hydrogram.types import Message
from datetime import datetime
from mimetypes import guess_type
from bot.clients import TelegramBot, get_worker_client, mark_worker_cooldown

try:
    from hydrogram.errors import FloodWait
except ImportError:
    try:
        from pyrogram.errors import FloodWait
    except ImportError:
        FloodWait = Exception

# Fast in-memory LRU cache for channel file messages (eliminates Telegram round-trips on seek/range requests)
_MESSAGE_CACHE: OrderedDict[tuple, tuple[Message, float]] = OrderedDict()
_CACHE_MAX_SIZE = 80
_CACHE_TTL = 3600  # 1 hour
MEDIA_READ_TIMEOUT = 18.0


async def iter_parallel_ranges(read, start, count):
    """Overlap four continuous four-chunk reads, with a bounded ordered buffer."""
    pending = deque()
    end = start + count

    def schedule(first):
        size = min(4, end - first)
        queue = asyncio.Queue(maxsize=size + 1)

        async def fill():
            try:
                async with aclosing(read(first, size)) as stream:
                    async for chunk in stream:
                        await queue.put(chunk)
            except Exception as error:
                await queue.put(error)
            else:
                await queue.put(None)

        pending.append((asyncio.create_task(fill()), queue))
        return first + size

    next_start = start
    try:
        while len(pending) < 4 and next_start < end:
            next_start = schedule(next_start)
        while pending:
            task, queue = pending[0]
            while True:
                chunk = await queue.get()
                if chunk is None:
                    break
                if isinstance(chunk, Exception):
                    raise chunk
                yield chunk
            await task
            pending.popleft()
            if next_start < end:
                next_start = schedule(next_start)
    finally:
        tasks = [task for task, _ in pending]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


async def iter_media_chunks(client, message, offset, limit):
    """Read bounded chunks, refreshing a stale reference once on the same client.

    Hydrogram can log FILE_REFERENCE_EXPIRED and end its iterator instead of
    raising it. Early EOF therefore needs the same recovery as that exception.
    """
    received = 0
    refreshed = False
    while received < limit:
        try:
            async with aclosing(client.stream_media(message, offset=offset + received, limit=limit - received)) as stream:
                while True:
                    try:
                        chunk = await asyncio.wait_for(anext(stream), timeout=MEDIA_READ_TIMEOUT)
                    except StopAsyncIteration:
                        break
                    received += 1
                    yield chunk
        except Exception as error:
            if type(error).__name__ not in ('FileReferenceExpired', 'FileReferenceEmpty', 'FileReferenceInvalid'):
                raise
        if received >= limit:
            return
        chat_id = getattr(getattr(message, 'chat', None), 'id', None)
        message_id = getattr(message, 'id', None)
        if refreshed or chat_id is None or message_id is None:
            raise IOError('Telegram media stream ended before the requested chunks arrived')
        refreshed = True
        message = await get_message(chat_id, message_id, client=client, force_refresh=True, allow_fallback=False)
        if message is None:
            raise IOError('Could not refresh the Telegram media reference for this worker')

async def get_media_chunk(client, message, offset):
    """Drain the limited stream so Hydrogram releases its transmission slot."""
    chunk = None
    async for data in iter_media_chunks(client, message, offset=offset, limit=1):
        chunk = data
    return chunk

def invalidate_message_cache(chat_id: int | str = None, message_id: int = None):
    """Evicts cached message(s) from memory across all worker clients immediately."""
    if chat_id is None and message_id is None:
        _MESSAGE_CACHE.clear()
        return

    keys_to_remove = [
        k for k in list(_MESSAGE_CACHE.keys())
        if (chat_id is None or str(k[1]) == str(chat_id)) and (message_id is None or k[2] == message_id)
    ]
    for k in keys_to_remove:
        _MESSAGE_CACHE.pop(k, None)

async def get_message(chat_id: int | str, message_id: int, client: Client = None, force_refresh: bool = False, allow_fallback: bool = True) -> Message | None:
    target_client = client or get_worker_client() or TelegramBot
    client_name = getattr(target_client, 'name', 'bot')
    cache_key = (client_name, chat_id, message_id)
    now = time.time()

    if force_refresh:
        _MESSAGE_CACHE.pop(cache_key, None)
    elif cache_key in _MESSAGE_CACHE:
        cached_msg, ts = _MESSAGE_CACHE[cache_key]
        if now - ts < _CACHE_TTL:
            _MESSAGE_CACHE.move_to_end(cache_key)
            return cached_msg
        else:
            _MESSAGE_CACHE.pop(cache_key, None)

    message = None

    try:
        message = await asyncio.wait_for(target_client.get_messages(chat_id=chat_id, message_ids=message_id), timeout=8.0)
        if message and message.empty:
            message = None
    except Exception as e:
        if isinstance(e, FloodWait):
            mark_worker_cooldown(client_name, getattr(e, 'value', 30))
        # If worker client does not have peer in memory, resolve chat and retry get_messages
        try:
            await asyncio.wait_for(target_client.get_chat(chat_id), timeout=8.0)
            message = await asyncio.wait_for(target_client.get_messages(chat_id=chat_id, message_ids=message_id), timeout=8.0)
            if message and message.empty:
                message = None
        except Exception:
            pass

        # Fallback to main TelegramBot if worker failed to resolve message
        if allow_fallback and not message and target_client != TelegramBot:
            try:
                target_client = TelegramBot
                client_name = getattr(target_client, 'name', 'bot')
                cache_key = (client_name, chat_id, message_id)
                message = await asyncio.wait_for(TelegramBot.get_messages(chat_id=chat_id, message_ids=message_id), timeout=8.0)
                if message and message.empty:
                    message = None
            except Exception:
                message = None

    if message:
        if len(_MESSAGE_CACHE) >= _CACHE_MAX_SIZE:
            _MESSAGE_CACHE.popitem(last=False)
        _MESSAGE_CACHE[cache_key] = (message, now)

    return message

def is_media_message(msg: Message) -> bool:
    if not msg:
        return False
    attributes = ('document', 'video', 'audio', 'voice', 'photo', 'video_note')
    return any(getattr(msg, attr, None) is not None for attr in attributes)

from bot.modules.static import format_duration

def get_file_properties(msg: Message) -> tuple[str, int, str, int, str]:
    if not msg:
        return None, 0, 'application/octet-stream', 0, 'N/A'
        
    attributes = (
        'video',
        'document',
        'audio',
        'voice',
        'photo',
        'video_note'
    )
    media = None
    file_type = None
    for attribute in attributes:
        media = getattr(msg, attribute, None)
        if media:
            file_type = attribute
            break

    if not media:
        return None, 0, 'application/octet-stream', 0, 'N/A'

    file_name = getattr(media, 'file_name', None)
    file_size = getattr(media, 'file_size', 0) or 0
    duration = getattr(media, 'duration', 0) or 0

    # If document, inspect attributes for video/audio duration
    if file_type == 'document' and not duration:
        for attr in getattr(media, 'attributes', []):
            if hasattr(attr, 'duration') and attr.duration:
                duration = attr.duration
                break

    # Extract clean file_name:
    # 1. Prefer message caption if it contains the episode/movie title as written in channel
    if msg.caption:
        first_line = msg.caption.strip().split('\n')[0].strip()
        if 3 < len(first_line) < 200:
            file_name = first_line
        else:
            file_name = getattr(media, 'file_name', None)
    else:
        file_name = getattr(media, 'file_name', None)

    # 2. Fallback to timestamp if neither caption nor media.file_name exists
    if not file_name:
        file_format = {
            'video': 'mp4',
            'audio': 'mp3',
            'voice': 'ogg',
            'photo': 'jpg',
            'video_note': 'mp4'
        }.get(file_type, 'bin')
        date = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        file_name = f'{file_type}-{date}.{file_format}'
    
    mime_type = getattr(media, 'mime_type', None)
    if not mime_type:
        mime_type = guess_type(file_name)[0] or 'application/octet-stream'

    duration_formatted = format_duration(duration)

    return file_name, file_size, mime_type, duration, duration_formatted
