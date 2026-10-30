import hmac
import hashlib
from datetime import datetime, timedelta, timezone
import logging
import re
import secrets
import time
from collections import deque

from quart import Blueprint, Response, make_response, redirect, render_template, request, url_for, jsonify, send_file
from werkzeug.sansio.http import is_resource_modified

from bot.config import Server
from bot.database import db
from bot.database.catalog import CATEGORIES, PAGE_SIZE, list_files, selected_files
from bot.modules import index_access
from bot.modules.static import get_human_size
from bot.modules.archive import archive_size, stream_archive, archive_filename, archive_disposition
from bot.modules.archive_cache import archive_cache, ARCHIVE_AGE
from bot.modules.activity import activity
from .error import abort

bp = Blueprint('catalog', __name__)
CSRF_COOKIE = 'gatestream_index_csrf'
_attempts = {}
logger = logging.getLogger('server')


@bp.after_request
async def archive_headers(response):
    if request.path.startswith(('/index/archive/', '/index/prepare/')):
        _private(response)
        response.headers['Referrer-Policy'] = 'no-referrer'
    return response


def _secure():
    return request.is_secure or Server.BASE_URL.startswith('https://')


def _private(response):
    response.headers['Cache-Control'] = 'private, no-store, max-age=0'
    response.headers['Vary'] = 'Cookie'
    response.headers['X-Robots-Tag'] = 'noindex, nofollow'
    response.headers['Referrer-Policy'] = 'same-origin'
    return response


def _csrf_token():
    value = request.cookies.get(CSRF_COOKIE, '')
    return value if 20 <= len(value) <= 128 else secrets.token_urlsafe(32)


def _csrf_valid(form):
    cookie = request.cookies.get(CSRF_COOKIE, '')
    value = form.get('csrf', '')
    return bool(cookie and len(cookie) <= 128 and len(value) <= 128 and hmac.compare_digest(cookie.encode(), value.encode()))


def _csrf_cookie(response, token):
    response.set_cookie(CSRF_COOKIE, token, max_age=index_access.SESSION_AGE,
                        httponly=True, secure=_secure(), samesite='Lax', path='/')


async def authenticated():
    token = request.cookies.get(index_access.COOKIE)
    if not token:
        return False
    try:
        return index_access.valid_session(token, await index_access.settings())
    except Exception as error:
        logger.error('Index access unavailable (%s).', type(error).__name__)
        return False


async def _login(error=None, status=200):
    csrf = _csrf_token()
    response = await make_response(await render_template('catalog.html', unlocked=False, section='link', signed_in=False, csrf=csrf, error=error), status)
    _csrf_cookie(response, csrf)
    return _private(response)


@bp.route('/index')
async def index_page():
    section = 'link' if request.args.get('section') == 'link' else 'index'
    signed_in = await authenticated() if section == 'link' else False
    if section == 'link' and not signed_in:
        return await _login()
    query = request.args.get('q', '').strip()[:100]
    category = request.args.get('category', 'all')
    if category not in CATEGORIES:
        category = 'all'
    try:
        page = max(1, min(100000, int(request.args.get('page', '1'))))
    except ValueError:
        page = 1
    try:
        data = await list_files(page, query, category, section)
    except Exception as error:
        logger.error('Index listing unavailable (%s).', type(error).__name__)
        return _private(await make_response('The media index is temporarily unavailable. Please try again.', 503))
    if page > data['pages']:
        return _private(redirect(url_for('catalog.index_page', q=query, category=category, section=section, page=data['pages'])))
    for item in data['files']:
        try:
            item['file_size'] = max(0, int(item.get('file_size') or 0))
        except (ValueError, TypeError):
            item['file_size'] = 0
    csrf = _csrf_token()
    response = await make_response(await render_template('catalog.html', unlocked=True, section=section, signed_in=signed_in, csrf=csrf,
        query=query, category=category, categories=CATEGORIES, page_size=PAGE_SIZE, get_human_size=get_human_size, **data))
    _csrf_cookie(response, csrf)
    return _private(response)


@bp.route('/index/login', methods=['POST'])
async def index_login():
    request.max_content_length = 4096
    form = await request.form
    if not _csrf_valid(form):
        return await _login('The sign-in form expired. Please try again.', 400)
    now = time.monotonic()
    for key in list(_attempts):
        while _attempts[key] and _attempts[key][0] < now - 300:
            _attempts[key].popleft()
        if not _attempts[key]:
            del _attempts[key]
    if len(_attempts) >= 1024:
        _attempts.pop(next(iter(_attempts)))
    attempts = _attempts.setdefault(request.remote_addr or 'unknown', deque())
    if len(attempts) >= 10:
        response = await _login('Too many attempts. Please try again in five minutes.', 429)
        response.headers['Retry-After'] = '300'
        return response
    attempts.append(now)
    try:
        document = await index_access.settings()
        valid = await index_access.verify_password(form.get('password', ''), document)
    except Exception as error:
        logger.error('Index sign-in unavailable (%s).', type(error).__name__)
        return await _login('Sign-in is temporarily unavailable. Please try again.', 503)
    if not valid:
        return await _login('Incorrect password. Please try again.', 401)
    attempts.clear()
    response = redirect(url_for('catalog.index_page', section='link'))
    response.set_cookie(index_access.COOKIE, index_access.session_token(document),
        max_age=index_access.SESSION_AGE, httponly=True, secure=_secure(), samesite='Lax', path='/')
    return _private(response)


@bp.route('/index/logout', methods=['POST'])
async def index_logout():
    request.max_content_length = 4096
    if not _csrf_valid(await request.form):
        return await _login('The sign-out form expired. Please try again.', 400)
    response = redirect(url_for('catalog.index_page', section='link'))
    response.delete_cookie(index_access.COOKIE, path='/')
    return _private(response)


@bp.route('/index/download', methods=['POST'])
async def download_selected():
    request.max_content_length = 16384
    form = await request.form
    if not _csrf_valid(form):
        return _private(await make_response('The selection form expired. Reload the catalog and try again.', 400))
    section = form.get('section', '')
    if section not in ('index', 'link'):
        return _private(await make_response('Invalid catalog section.', 400))
    mode = form.get('mode', 'prepare')
    if mode not in ('stream', 'prepare'):
        return _private(await make_response('Choose Start immediately or Prepare first.', 400))
    if section == 'link' and not await authenticated():
        return await _login('Enter the password before downloading selected Link files.', 401)
    try:
        filename = archive_filename(form.get('zip_name', ''), section)
    except ValueError as error:
        return _private(await make_response(str(error), 400))
    codes = list(dict.fromkeys(form.getlist('code')))
    if not 1 <= len(codes) <= PAGE_SIZE or any(not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', code) for code in codes):
        return _private(await make_response(f'Select between 1 and {PAGE_SIZE} files on this page.', 400))
    try:
        files = await selected_files(codes, section)
    except Exception as error:
        logger.error('Selection lookup unavailable (%s).', type(error).__name__)
        return _private(await make_response('The catalog is temporarily unavailable. Please try again.', 503))
    if len(files) != len(codes):
        return _private(await make_response('Some selected files are no longer in this category. Reload and select again.', 409))
    try:
        token = secrets.token_urlsafe(32)
        ticket = dict(_id=token, codes=codes, section=section, mode=mode, archive_version=3, filename=filename,
            files=[{key: item[key] for key in ('code', 'file_name', 'file_size')} for item in files],
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=ARCHIVE_AGE))
        if section == 'link':
            settings = await index_access.settings()
            ticket['revision'] = hashlib.sha256(settings['session_secret'].encode()).hexdigest()
        await db.db['download_tickets'].insert_one(ticket)
        from .main import transmit_file

        async def download(code):
            return await transmit_file(code, range_allowed=False, internal=True, parallel=True)

        await archive_cache.start(token, ticket['files'], download, ticket['expires_at'].timestamp(), progressive=mode == 'stream')
    except ValueError as error:
        return _private(await make_response(str(error), 413))
    except Exception as error:
        logger.error('ZIP link creation failed (%s).', type(error).__name__)
        return _private(await make_response('Unable to create the download link. Please try again.', 503))
    link = url_for('catalog.download_archive', token=token, filename=filename)
    if request.accept_mimetypes.best == 'application/json':
        return _private(jsonify(url=link, mode=mode, status_url=url_for('catalog.archive_status', token=token)))
    return _private(redirect(link if mode == 'stream' else url_for('catalog.prepare_archive', token=token), code=303))


async def _archive_ticket(token):
    if not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
        abort(404, 'Download link not found.')
    try:
        ticket = await db.db['download_tickets'].find_one({'_id': token})
        revoked = False
        if ticket and ticket['section'] == 'link':
            settings = await index_access.settings()
            revision = hashlib.sha256(settings['session_secret'].encode()).hexdigest()
            revoked = not hmac.compare_digest(ticket.get('revision', ''), revision)
    except Exception as error:
        logger.error('ZIP link lookup failed (%s).', type(error).__name__)
        abort(503, 'The download is temporarily unavailable. Please try again.')
    if not ticket or datetime.now(timezone.utc) >= ticket['expires_at'].replace(tzinfo=timezone.utc):
        abort(410, 'Download link expired. Select the files again.')
    if revoked:
        abort(410, 'The password changed. Select the files again.')
    if ticket.get('archive_version') not in (2, 3):
        abort(410, 'Create a new selection to use resumable ZIP downloads.')
    return ticket


@bp.route('/index/archive/<string:token>/status')
async def archive_status(token):
    ticket = await _archive_ticket(token)
    if ticket.get('mode') == 'stream' and ticket['archive_version'] == 2:
        return jsonify(state='ready', bytes=0, total=archive_size(ticket['files']))
    state = archive_cache.status(token, archive_size(ticket['files']))
    return jsonify(state), 200 if state['state'] == 'ready' else 202 if state['state'] in ('queued', 'preparing') else 410


@bp.route('/index/prepare/<string:token>')
async def prepare_archive(token):
    ticket = await _archive_ticket(token)
    link = url_for('catalog.download_archive', token=token, filename=ticket.get('filename', f'GateStream-{ticket["section"]}.zip'))
    if ticket.get('mode') == 'stream':
        return redirect(link)
    state = archive_cache.status(token, archive_size(ticket['files']))
    if state['state'] == 'ready':
        return redirect(link)
    return await render_template('prepare.html', state=state, section=ticket['section']), 202 if state['state'] in ('queued', 'preparing') else 410


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


@bp.route('/index/archive/<string:token>/<string:filename>')
async def download_archive(token, filename):
    ticket = await _archive_ticket(token)
    from .main import download_client
    name = ticket.get('filename', f'GateStream-{ticket["section"]}.zip')
    try:
        files = await selected_files(ticket['codes'], ticket['section'])
    except Exception:
        abort(503, 'The catalog is temporarily unavailable. Please try again.')
    current = [{key: item[key] for key in ('code', 'file_name', 'file_size')} for item in files]
    if current != ticket['files']:
        abort(409, 'A selected file changed or was removed. Select the files again.')
    if ticket.get('mode') == 'stream' and ticket['archive_version'] == 2:
        from .main import transmit_file

        async def download(code):
            return await transmit_file(code, range_allowed=False, internal=True)

        length = archive_size(ticket['files'])
        body = None if request.method == 'HEAD' else activity.track_stream(
            stream_archive(ticket['files'], download), length, f'zip:{token}',
            download=('zip', 0, length, download_client()))
        response = Response(body, content_type='application/zip')
        response.automatically_set_content_length = False
        response.headers.update({
            'Content-Disposition': archive_disposition(name),
            'Content-Length': str(length), 'Accept-Ranges': 'none', 'X-Content-Type-Options': 'nosniff',
        })
        response.timeout = None
        return response
    state = archive_cache.status(token, archive_size(ticket['files']))
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
        as_attachment=True, attachment_filename=ticket.get('filename', f'GateStream-{ticket["section"]}.zip'))
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
