import asyncio
from datetime import datetime, timedelta, timezone
import logging
import re
import secrets

from quart import Blueprint, Response, redirect, render_template, request, url_for, jsonify, send_file
from werkzeug.sansio.http import is_resource_modified

from bot.database import db
from bot.modules import personal_library
from bot.modules.static import get_human_size
from bot.modules.archive import archive_size, archive_disposition, stream_archive
from bot.modules.archive_cache import archive_cache, ARCHIVE_AGE, ArchiveCapacityError
from bot.modules.activity import activity
from .error import abort

bp = Blueprint('catalog', __name__)
logger = logging.getLogger('server')
_direct_stream_slots = asyncio.Semaphore(2)


@bp.after_request
async def archive_headers(response):
    if request.path.startswith(('/index/archive/', '/index/prepare/')):
        _private(response)
        response.headers['Referrer-Policy'] = 'no-referrer'
    return response


def _private(response):
    response.headers['Cache-Control'] = 'private, no-store, max-age=0'
    response.headers['Vary'] = 'Cookie'
    response.headers['X-Robots-Tag'] = 'noindex, nofollow'
    response.headers['Referrer-Policy'] = 'same-origin'
    return response


def archive_members(files):
    return [{key: item[key] for key in ('code', 'file_name', 'file_size')} for item in files]


async def _download_member(code):
    from .main import transmit_file

    return await transmit_file(code, range_allowed=False, internal=True, parallel=True)


async def create_archive(files, filename, *, folder_id, user_id):
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(seconds=ARCHIVE_AGE)
    ticket = dict(_id=token, section='personal', mode='stream', archive_version=3, filename=filename,
                  files=archive_members(files), expires_at=expires, folder_id=folder_id, user_id=user_id)
    tickets = db.db['download_tickets']
    await tickets.insert_one(ticket)
    try:
        await archive_cache.start(token, ticket['files'], _download_member, expires.timestamp(), progressive=True)
    except ArchiveCapacityError:
        # Keep the existing cache and its resumable links. Stream this ZIP on demand.
        await tickets.update_one({'_id': token}, {'$set': {'mode': 'direct'}})
    return token


async def _archive_ticket(token):
    if not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
        abort(404, 'Download link not found.')
    try:
        ticket = await db.db['download_tickets'].find_one({'_id': token})
    except Exception as error:
        logger.error('ZIP link lookup failed (%s).', type(error).__name__)
        abort(503, 'The download is temporarily unavailable. Please try again.')
    if not ticket or datetime.now(timezone.utc) >= ticket['expires_at'].replace(tzinfo=timezone.utc):
        abort(410, 'Download link expired. Select the files again.')
    if ticket.get('section') != 'personal':
        abort(410, 'This ZIP link belongs to the retired channel catalog.')
    if ticket.get('archive_version') != 3:
        abort(410, 'Open the folder to create a new download link.')
    return ticket


@bp.route('/index/archive/<string:token>/status')
async def archive_status(token):
    ticket = await _archive_ticket(token)
    total = archive_size(ticket['files'])
    if ticket.get('mode') == 'direct':
        return jsonify(state='ready', bytes=0, total=total, source_speed_bps=0)
    state = archive_cache.status(token, total)
    return jsonify(state), 200 if state['state'] == 'ready' else 202 if state['state'] in ('queued', 'preparing') else 410


@bp.route('/index/prepare/<string:token>')
async def prepare_archive(token):
    ticket = await _archive_ticket(token)
    link = url_for('catalog.download_archive', token=token, filename=ticket['filename'])
    if ticket.get('mode') in ('stream', 'direct'):
        return redirect(link)
    state = archive_cache.status(token, archive_size(ticket['files']))
    if state['state'] == 'ready':
        return redirect(link)
    back = url_for('personal_library.share_folder', folder_id=ticket['folder_id'])
    return await render_template('prepare.html', state=state, back=back, get_human_size=get_human_size), 202 if state['state'] in ('queued', 'preparing') else 410


async def _archive_range(response, total):
    """Use identical HTTP range/validator behavior for finished and growing ZIPs."""
    await response.make_conditional(request)
    bounds = (0, total)
    if response.status_code == 200 and request.headers.get('Range') and (
        not request.headers.get('If-Range') or not is_resource_modified(
            http_range=request.headers['Range'], http_if_range=request.headers['If-Range'],
            etag=response.headers.get('ETag'), last_modified=response.last_modified, ignore_if_range=False)):
        # Normalize suffix/open ranges before seeking (including Quart 0.22).
        requested = request.range
        bounds = requested.range_for_length(total) if requested else None
        if bounds is None:
            response.status_code = 416
            response.set_data(b'')
            response.headers['Content-Range'] = f'bytes */{total}'
        else:
            start, stop = bounds
            response.status_code = 206
            response.content_length = stop - start
            response.headers['Content-Range'] = f'bytes {start}-{stop-1}/{total}'
    response.headers['Accept-Ranges'] = 'bytes'
    return bounds if response.status_code in (200, 206) else None


async def _direct_zip(files):
    from contextlib import aclosing

    async with _direct_stream_slots, aclosing(stream_archive(files, _download_member)) as stream:
        async for chunk in stream:
            yield chunk


@bp.route('/index/archive/<string:token>/<string:filename>')
async def download_archive(token, filename):
    ticket = await _archive_ticket(token)
    from .main import download_client
    name = ticket['filename']
    try:
        files = await personal_library.folder_files(ticket['user_id'], ticket['folder_id'])
    except Exception:
        abort(503, 'The folder is temporarily unavailable. Please try again.')
    if archive_members(files) != ticket['files']:
        abort(409, 'A selected file changed or was removed. Select the files again.')
    total = archive_size(ticket['files'])
    if ticket.get('mode') == 'direct':
        if request.method == 'GET' and _direct_stream_slots.locked():
            return 'ZIP downloads are busy. Try this link again shortly.', 503, {'Retry-After': '3'}
        response = Response(content_type='application/zip')
        response.content_length = total
        response.headers['Content-Disposition'] = archive_disposition(name)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Accept-Ranges'] = 'none'
        if request.method == 'GET':
            response.response = response.iterable_body_class(activity.track_stream(
                _direct_zip(ticket['files']), total, f'zip:{token}',
                download=('zip', 0, total, download_client())))
        response.timeout = None
        return response
    state = archive_cache.status(token, total)
    if ticket.get('mode') == 'stream':
        if state['state'] not in ('queued', 'preparing', 'ready'):
            abort(410, 'The live ZIP is no longer available. Select the files again.')
        response = Response(content_type='application/zip')
        response.content_length = state['total']
        response.set_etag(f'gatestream-{token}')
        response.last_modified = ticket['expires_at'].replace(tzinfo=timezone.utc) - timedelta(seconds=ARCHIVE_AGE)
        response.headers['Content-Disposition'] = archive_disposition(name)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        bounds = await _archive_range(response, state['total'])
        if request.method == 'GET' and bounds is not None:
            response.response = response.iterable_body_class(activity.track_stream(
                archive_cache.read(token, *bounds), response.content_length, f'zip:{token}',
                download=('zip', bounds[0], state['total'], download_client())))
        response.timeout = None
        return response
    if state['state'] in ('queued', 'preparing'):
        return 'The ZIP is still being prepared. Wait for the download button.', 503, {'Retry-After': '2'}
    if state['state'] != 'ready':
        abort(410, 'The prepared ZIP is no longer available. Select the files again.')
    response = await send_file(archive_cache.path(token), mimetype='application/zip',
        as_attachment=True, attachment_filename=name)
    response.headers['Content-Disposition'] = archive_disposition(name)
    bounds = await _archive_range(response, state['total'])
    if bounds is not None:
        await response.response.make_conditional(*bounds)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    if request.method == 'GET' and response.status_code in (200, 206):
        body = response.response
        body.buffer_size = 1024 * 1024

        async def chunks():
            async with body as source:
                async for chunk in source:
                    yield chunk

        response.response = response.iterable_body_class(activity.track_stream(chunks(), response.content_length, f'zip:{token}',
            download=('zip', bounds[0], state['total'], download_client())))
    response.timeout = None
    return response
