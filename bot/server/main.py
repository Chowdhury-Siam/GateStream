import asyncio
from logging import getLogger
from quart import Blueprint, Response, request, render_template, jsonify
from math import ceil
from re import match as re_match
from .error import abort
from bot.clients import get_worker_client, TelegramBot, mark_worker_cooldown, worker_clients
from bot.config import Telegram, Server
from bot.database import db
from bot.database.files import get_file, add_bandwidth_bytes
from bot.modules.telegram import get_message, get_file_properties
from bot.modules.static import get_human_size
from bot.modules.memory import flush_ram, check_memory_circuit_breaker
from bot.modules.cache import get_cached_chunk, put_cached_chunk, is_head_or_tail, init_cache, is_chunk_cached, prefetch_tail_chunk, prefetch_head_chunk

try:
    from hydrogram.errors import FloodWait
except ImportError:
    try:
        from pyrogram.errors import FloodWait
    except ImportError:
        FloodWait = Exception

logger = getLogger('server')

bp = Blueprint('main', __name__)
init_cache()

@bp.route('/')
async def home():
    username = getattr(TelegramBot.me, 'username', None) or Telegram.BOT_USERNAME
    return await render_template('home.html', bot_username=username)

@bp.route('/ping')
@bp.route('/health')
async def health_check():
    return "OK", 200

# Malicious vulnerability scanners and abusive scrapers to block immediately
BLOCKED_AGENTS = (
    'sqlmap', 'nikto', 'masscan', 'nmap', 'dirbuster', 'gobuster',
    'zgrab', 'wpscan', 'acunetix', 'nessus', 'havij', 'openvas',
    'scrapy', 'censys', 'shodan'
)

@bp.before_request
async def security_and_bot_check():
    # Always allow health checks, pings, and the home page
    if request.path in ('/ping', '/health', '/'):
        return None

    user_agent = (request.headers.get('User-Agent') or '').lower()

    # Block automated vulnerability scanners and abusive crawlers
    if any(blocked in user_agent for blocked in BLOCKED_AGENTS):
        return jsonify({'status': 'error', 'message': 'Access forbidden'}), 403

    return None

# ==================== STREAM & DOWNLOAD ROUTES ====================

@bp.route('/dl/<string:file_code>')
async def transmit_file(file_code):
    # Lookup file record in MongoDB
    doc = await get_file(file_code)
    if not doc:
        abort(404, 'File not found or link has expired.')

    channel_id = doc.get('channel_id')
    message_id = doc.get('message_id')

    # Lazy message resolution: Contact Telegram ONLY if metadata is missing or uncached chunks are needed!
    worker = get_worker_client() or TelegramBot
    file_msg = None

    async def get_target_msg():
        nonlocal file_msg
        if file_msg is not None:
            return file_msg
        file_msg = await get_message(channel_id, message_id, client=TelegramBot)
        return file_msg

    file_name = doc.get('file_name')
    file_size = doc.get('file_size')
    mime_type = doc.get('mime_type')

    # Fallback to inspecting message ONLY if metadata in database is incomplete
    if not file_name or not file_size or not mime_type:
        msg = await get_target_msg()
        if not msg:
            abort(404, 'Media message not found in channel.')
        f_name, f_size, m_type = get_file_properties(msg)
        file_name = file_name or f_name
        file_size = file_size or f_size
        mime_type = mime_type or m_type

    range_header = request.headers.get('Range')
    start = 0
    end = file_size - 1
    chunk_size = 1024 * 1024  # 1 MB

    if range_header:
        range_match = re_match(r'bytes=(\d+)-(\d*)', range_header)
        if range_match:
            start = int(range_match.group(1))
            end = int(range_match.group(2)) if range_match.group(2) else file_size - 1
            if start > end or start >= file_size:
                abort(416, 'Requested range not satisfiable')
        else:
            abort(400, 'Invalid Range header')

    total_bytes_to_stream = end - start + 1
    content_length = total_bytes_to_stream
    # Ensure a valid video MIME type so browsers stream inline instead of forcing a download
    if not mime_type or mime_type == 'application/octet-stream':
        if file_name and file_name.lower().endswith('.mkv'):
            mime_type = 'video/x-matroska'
        elif file_name and file_name.lower().endswith('.webm'):
            mime_type = 'video/webm'
        else:
            mime_type = 'video/mp4'

    # Default to 'attachment' so direct download links automatically download the file in browsers.
    # Use 'inline' only when ?stream=1 or ?inline=1 is specified (e.g. by HTML5 video player in player.html).
    is_streaming = (request.args.get('stream') or request.args.get('inline')) and not (request.args.get('dl') or request.args.get('download'))
    disposition = 'inline' if is_streaming else 'attachment'

    headers = {
        'Content-Type': mime_type,
        'Content-Disposition': f'{disposition}; filename="{file_name}"',
        'Accept-Ranges': 'bytes',
        'Content-Length': str(content_length),
        'Connection': 'keep-alive',
        'Keep-Alive': 'timeout=300, max=1000',
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Expose-Headers': 'Content-Range, Accept-Ranges, Content-Length',
        'X-Content-Type-Options': 'nosniff',
    }
    if range_header:
        headers['Content-Range'] = f'bytes {start}-{end}/{file_size}'
    status_code = 206 if range_header else 200

    async def file_stream():
        bytes_streamed = 0
        current_start = start
        offset = current_start // chunk_size
        remaining_total = end - current_start + 1
        chunks_needed = ceil(remaining_total / chunk_size)
        total_chunks = ceil(file_size / chunk_size)

        curr_offset = offset
        curr_chunks_needed = chunks_needed

        # Proactively trigger detached background pre-fetch of both Head and Tail if opening chunk 0
        if offset == 0:
            if not is_chunk_cached(file_code, 0):
                asyncio.create_task(prefetch_head_chunk(file_code, total_chunks, target_msg=file_msg, channel_id=channel_id, message_id=message_id))
            if total_chunks > 4 and not is_chunk_cached(file_code, total_chunks - 1):
                asyncio.create_task(prefetch_tail_chunk(file_code, total_chunks, target_msg=file_msg, channel_id=channel_id, message_id=message_id))

        # 1. Fast Path: Serve any consecutive leading chunks from Head & Tail SSD cache (0.5ms response time)
        # Supports in-flight waiting if chunk is currently being pre-cached by a worker!
        while curr_chunks_needed > 0:
            cached_data = await get_cached_chunk(file_code, curr_offset, wait_in_flight=True)
            if cached_data is None:
                break

            chunk = cached_data
            if bytes_streamed == 0:
                trim_start = current_start % chunk_size
                if trim_start > 0:
                    chunk = chunk[trim_start:]

            rem_bytes = content_length - bytes_streamed
            if rem_bytes <= 0:
                break
            if len(chunk) > rem_bytes:
                chunk = chunk[:rem_bytes]

            yield chunk
            bytes_streamed += len(chunk)
            del chunk
            curr_offset += 1
            curr_chunks_needed -= 1

            if bytes_streamed >= content_length:
                break

        # If all requested data was served directly from cache (0ms delay), we are done without touching Telegram!
        if curr_chunks_needed <= 0 or bytes_streamed >= content_length:
            if bytes_streamed > 0:
                await add_bandwidth_bytes(bytes_streamed)
            check_memory_circuit_breaker()
            flush_ram()
            return

        # 2. Dual-Engine Streaming Architecture
        # -------------------------------------------------------------------------
        # Mode A: Multi-Worker Concurrent Chunk Pipeliner
        # Activated for single-connection video playback (is_streaming=True, e.g. StreamHub / ExoPlayer).
        # Pipelines 3-4 chunks in parallel across the worker pool into RAM (approx 4 MB buffer).
        # Eliminates MTProto round-trip latency and bypasses Telegram's single-session speed limit,
        # delivering 2.5 - 3.5+ MB/s over a single HTTP connection!
        #
        # Mode B: Resilient Sequential Continuous Stream
        # Dedicated healthy worker streaming directly from MTProto.
        # Ideal for multi-connection download managers (ABDM, IDM) where 8-16 connections
        # each receive their own dedicated worker without multiplying task overhead.
        # -------------------------------------------------------------------------
        end_chunk = curr_offset + curr_chunks_needed

        if is_streaming and curr_chunks_needed > 1:
            PREFETCH_CONCURRENCY = min(4, len(worker_clients))
            pending_tasks: dict[int, asyncio.Task] = {}
            next_to_schedule = curr_offset

            async def fetch_chunk(target_idx: int) -> bytes | None:
                # 1. Check local SSD cache (0.5ms)
                cached = await get_cached_chunk(file_code, target_idx, wait_in_flight=True)
                if cached is not None:
                    return cached

                # 2. Try across worker pool with fallback
                for attempt in range(3):
                    worker = get_worker_client() or TelegramBot
                    w_name = getattr(worker, 'name', 'bot')
                    try:
                        msg = await get_message(channel_id, message_id, client=worker)
                        if not msg and worker != TelegramBot:
                            worker = TelegramBot
                            w_name = 'TelegramBot'
                            msg = await get_message(channel_id, message_id, client=TelegramBot)
                        if not msg:
                            continue

                        async def _pull():
                            async for c in worker.stream_media(msg, offset=target_idx, limit=1):
                                return c
                            return None

                        chunk = await asyncio.wait_for(_pull(), timeout=18.0)
                        if chunk:
                            if is_head_or_tail(target_idx, total_chunks):
                                asyncio.create_task(put_cached_chunk(file_code, target_idx, chunk, total_chunks))
                            return chunk
                    except asyncio.TimeoutError:
                        mark_worker_cooldown(w_name, 15)
                        logger.warning("Worker %s timed out (18s) on chunk %d, rotating...", w_name, target_idx)
                    except Exception as e:
                        err_name = type(e).__name__
                        if any(x in err_name for x in ("ChannelPrivate", "ChatAdminRequired", "UserNotParticipant")):
                            mark_worker_cooldown(w_name, 600)
                        elif isinstance(e, FloodWait):
                            wait_sec = getattr(e, 'value', 30)
                            mark_worker_cooldown(w_name, wait_sec)
                        else:
                            mark_worker_cooldown(w_name, 15)
                        logger.warning("Worker %s error on chunk %d: %s", w_name, target_idx, e)

                # 3. Final safety retry: TelegramBot with fresh message
                try:
                    fresh_msg = await get_message(channel_id, message_id, client=TelegramBot, force_refresh=True)
                    if fresh_msg:
                        async def _pull_fb():
                            async for c in TelegramBot.stream_media(fresh_msg, offset=target_idx, limit=1):
                                return c
                            return None
                        return await asyncio.wait_for(_pull_fb(), timeout=18.0)
                except Exception as fb_err:
                    logger.error("Final TelegramBot fallback failed on chunk %d: %s", target_idx, fb_err)

                return None

            # Pre-fill pipeline across worker pool
            while len(pending_tasks) < PREFETCH_CONCURRENCY and next_to_schedule < end_chunk:
                pending_tasks[next_to_schedule] = asyncio.create_task(fetch_chunk(next_to_schedule))
                next_to_schedule += 1

            try:
                while curr_offset < end_chunk and bytes_streamed < content_length:
                    if curr_offset not in pending_tasks:
                        pending_tasks[curr_offset] = asyncio.create_task(fetch_chunk(curr_offset))

                    task = pending_tasks.pop(curr_offset)
                    chunk = await task

                    # Immediately schedule next chunk to keep worker pipeline full
                    while len(pending_tasks) < PREFETCH_CONCURRENCY and next_to_schedule < end_chunk:
                        pending_tasks[next_to_schedule] = asyncio.create_task(fetch_chunk(next_to_schedule))
                        next_to_schedule += 1

                    # Emergency retry before dropping chunk
                    retry_em = 0
                    while chunk is None and retry_em < 3:
                        retry_em += 1
                        logger.warning("Retrying chunk %d (attempt %d/3)...", curr_offset, retry_em)
                        await asyncio.sleep(0.5)
                        chunk = await fetch_chunk(curr_offset)

                    if chunk is None:
                        logger.warning("Failed to retrieve chunk %d for %s after emergency retries", curr_offset, file_code)
                        break

                    if bytes_streamed == 0:
                        trim_start = current_start % chunk_size
                        if trim_start > 0:
                            chunk = chunk[trim_start:]

                    rem_bytes = content_length - bytes_streamed
                    if rem_bytes <= 0:
                        break

                    if len(chunk) > rem_bytes:
                        chunk = chunk[:rem_bytes]

                    yield chunk
                    bytes_streamed += len(chunk)
                    del chunk
                    curr_offset += 1

                    if curr_offset % 20 == 0:
                        check_memory_circuit_breaker()

            except (asyncio.CancelledError, GeneratorExit):
                pass
            finally:
                for t in pending_tasks.values():
                    if not t.done():
                        t.cancel()
                pending_tasks.clear()
                if bytes_streamed > 0:
                    await add_bandwidth_bytes(bytes_streamed)
                check_memory_circuit_breaker()
                flush_ram()

        else:
            # Mode B: Continuous Sequential Stream for multi-connection downloads
            retry_count = 0
            MAX_RETRIES = 12

            try:
                while curr_offset < end_chunk and bytes_streamed < content_length and retry_count < MAX_RETRIES:
                    worker_client = get_worker_client() or TelegramBot
                    worker_name = getattr(worker_client, 'name', 'bot')

                    try:
                        msg = await get_message(channel_id, message_id, client=worker_client)
                        if not msg and worker_client != TelegramBot:
                            worker_client = TelegramBot
                            worker_name = 'TelegramBot'
                            msg = await get_message(channel_id, message_id, client=TelegramBot)
                        if not msg:
                            logger.warning("Could not retrieve message for %s at chunk %d", file_code, curr_offset)
                            break
                    except Exception as msg_err:
                        logger.warning("Error fetching message for worker %s: %s", worker_name, msg_err)
                        mark_worker_cooldown(worker_name, 15)
                        retry_count += 1
                        await asyncio.sleep(0.5)
                        continue

                    try:
                        async for chunk in worker_client.stream_media(msg, offset=curr_offset):
                            if is_head_or_tail(curr_offset, total_chunks):
                                asyncio.create_task(put_cached_chunk(file_code, curr_offset, chunk, total_chunks))

                            if bytes_streamed == 0:
                                trim_start = current_start % chunk_size
                                if trim_start > 0:
                                    chunk = chunk[trim_start:]

                            rem_bytes = content_length - bytes_streamed
                            if rem_bytes <= 0:
                                return

                            if len(chunk) > rem_bytes:
                                chunk = chunk[:rem_bytes]

                            yield chunk
                            bytes_streamed += len(chunk)
                            del chunk
                            curr_offset += 1
                            retry_count = 0

                            if curr_offset % 20 == 0:
                                check_memory_circuit_breaker()

                            if bytes_streamed >= content_length or curr_offset >= end_chunk:
                                return

                    except (asyncio.CancelledError, GeneratorExit):
                        raise
                    except Exception as stream_err:
                        retry_count += 1
                        err_name = type(stream_err).__name__
                        logger.warning("Stream worker %s encountered %s at chunk %d (retry %d/%d). Rotating worker...",
                                       worker_name, err_name, curr_offset, retry_count, MAX_RETRIES)
                        if any(x in err_name for x in ("ChannelPrivate", "ChatAdminRequired", "UserNotParticipant")):
                            mark_worker_cooldown(worker_name, 600)
                        elif isinstance(stream_err, FloodWait):
                            wait_sec = getattr(stream_err, 'value', 30)
                            mark_worker_cooldown(worker_name, wait_sec)
                        else:
                            mark_worker_cooldown(worker_name, 15)
                        await asyncio.sleep(0.5)

            except (asyncio.CancelledError, GeneratorExit):
                pass
            finally:
                if bytes_streamed > 0:
                    await add_bandwidth_bytes(bytes_streamed)
                check_memory_circuit_breaker()
                flush_ram()

    return Response(file_stream(), headers=headers, status=status_code)

@bp.route('/stream/<string:file_code>')
async def stream_file(file_code):
    doc = await get_file(file_code)
    if not doc:
        abort(404, 'File not found or link has expired.')

    media_url = f'{Server.BASE_URL}/dl/{file_code}?stream=1'
    download_url = f'{Server.BASE_URL}/dl/{file_code}'
    file_name = doc.get('file_name', 'Play Video')
    file_size_str = get_human_size(doc.get('file_size', 0))
    duration_str = doc.get('duration_formatted', '')
    bot_username = getattr(TelegramBot.me, 'username', None) or Telegram.BOT_USERNAME or ''

    return await render_template(
        'player.html',
        mediaLink=media_url,
        downloadLink=download_url,
        fileName=file_name,
        fileSize=file_size_str,
        duration=duration_str,
        fileCode=file_code,
        botUsername=bot_username
    )

# ==================== REST API ENDPOINTS FOR STREAMHUB ====================

@bp.route('/api/batch/<string:batch_id>')
async def api_get_batch(batch_id):
    """Returns structured JSON for a specific anime or web series batch."""
    for col in (db.anime, db.webseries):
        if col is not None:
            doc = await col.find_one({'_id': batch_id})
            if doc:
                episodes = []
                for ep in doc.get('episodes', []):
                    code = ep['code']
                    episodes.append({
                        'episode_num': ep.get('episode_num', 1),
                        'file_name': ep.get('file_name', ''),
                        'file_size': ep.get('file_size', 0),
                        'size_formatted': get_human_size(ep.get('file_size', 0)),
                        'duration': ep.get('duration', 0),
                        'duration_formatted': ep.get('duration_formatted', 'N/A'),
                        'mime_type': ep.get('mime_type', ''),
                        'stream_url': f"{Server.BASE_URL}/stream/{code}",
                        'download_url': f"{Server.BASE_URL}/dl/{code}",
                        'code': code
                    })
                return jsonify({
                    'status': 'success',
                    'batch_id': batch_id,
                    'title': doc.get('title', ''),
                    'category': doc.get('category', ''),
                    'channel_id': doc.get('channel_id'),
                    'total_episodes': len(episodes),
                    'episodes': episodes
                }), 200

    return jsonify({'status': 'error', 'message': 'Batch not found'}), 404

@bp.route('/api/file/<string:file_code>')
async def api_get_file(file_code):
    """Returns structured JSON metadata and streaming links for a single file/movie."""
    doc = await get_file(file_code)
    if not doc:
        return jsonify({'status': 'error', 'message': 'File not found'}), 404

    code = doc.get('code', file_code)
    return jsonify({
        'status': 'success',
        'code': code,
        'file_name': doc.get('file_name', ''),
        'file_size': doc.get('file_size', 0),
        'size_formatted': get_human_size(doc.get('file_size', 0)),
        'duration': doc.get('duration', 0),
        'duration_formatted': doc.get('duration_formatted', 'N/A'),
        'mime_type': doc.get('mime_type', ''),
        'category': doc.get('category', 'movies'),
        'stream_url': f"{Server.BASE_URL}/stream/{code}",
        'download_url': f"{Server.BASE_URL}/dl/{code}"
    }), 200

@bp.route('/api/movies')
async def api_get_movies():
    """Lists indexed movies."""
    if db.movies is None:
        return jsonify({'status': 'error', 'message': 'Database not connected'}), 500
    
    cursor = db.movies.find().sort('created_at', -1).limit(100)
    movies = []
    async for doc in cursor:
        code = doc.get('code', doc.get('_id'))
        movies.append({
            'code': code,
            'file_name': doc.get('file_name', ''),
            'file_size': doc.get('file_size', 0),
            'size_formatted': get_human_size(doc.get('file_size', 0)),
            'duration': doc.get('duration', 0),
            'duration_formatted': doc.get('duration_formatted', 'N/A'),
            'stream_url': f"{Server.BASE_URL}/stream/{code}",
            'download_url': f"{Server.BASE_URL}/dl/{code}"
        })
    return jsonify({'status': 'success', 'count': len(movies), 'movies': movies}), 200
