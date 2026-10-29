import hmac
import hashlib
from datetime import datetime, timedelta, timezone
import logging
import re
import secrets
import time
from collections import deque

from quart import Blueprint, Response, make_response, redirect, render_template, request, stream_with_context, url_for, jsonify

from bot.config import Server
from bot.database import db
from bot.database.catalog import CATEGORIES, PAGE_SIZE, list_files, selected_files
from bot.modules import index_access
from bot.modules.static import get_human_size

bp = Blueprint('catalog', __name__)
CSRF_COOKIE = 'gatestream_index_csrf'
_attempts = {}
logger = logging.getLogger('server')


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
    if section == 'link' and not await authenticated():
        return await _login('Enter the password before downloading selected Link files.', 401)
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
        ticket = dict(_id=token, codes=codes, section=section,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
        if section == 'link':
            settings = await index_access.settings()
            ticket['revision'] = hashlib.sha256(settings['session_secret'].encode()).hexdigest()
        await db.db['download_tickets'].insert_one(ticket)
    except Exception as error:
        logger.error('ZIP link creation failed (%s).', type(error).__name__)
        return _private(await make_response('Unable to create the download link. Please try again.', 503))
    link = url_for('catalog.download_archive', token=token, filename=f'GateStream-{section}.zip')
    if request.accept_mimetypes.best == 'application/json':
        return _private(jsonify(url=link))
    return _private(redirect(link, code=303))


@bp.route('/index/archive/<string:token>/<string:filename>')
async def download_archive(token, filename):
    if not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
        return _private(await make_response('Download link not found.', 404))
    try:
        ticket = await db.db['download_tickets'].find_one({'_id': token})
        if not ticket:
            return _private(await make_response('Download link expired. Select the files again.', 410))
        expiry = ticket['expires_at'].replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) >= expiry:
            return _private(await make_response('Download link expired. Select the files again.', 410))
        if ticket['section'] == 'link':
            settings = await index_access.settings()
            revision = hashlib.sha256(settings['session_secret'].encode()).hexdigest()
            if not hmac.compare_digest(ticket.get('revision', ''), revision):
                return _private(await make_response('The password changed. Select the files again.', 410))
        files = await selected_files(ticket['codes'], ticket['section'])
    except Exception as error:
        logger.error('ZIP link lookup failed (%s).', type(error).__name__)
        return _private(await make_response('The download is temporarily unavailable. Please try again.', 503))
    if len(files) != len(ticket['codes']):
        return _private(await make_response('A selected file was removed. Select the files again.', 409))
    from bot.modules.archive import stream_archive, archive_size
    from .main import transmit_file

    async def download(code):
        return await transmit_file(code, range_allowed=False)

    @stream_with_context
    async def archive():
        try:
            async for chunk in stream_archive(files, download):
                yield chunk
        except Exception as error:
            logger.error('ZIP transfer interrupted (%s).', type(error).__name__)
            raise

    try:
        length = archive_size(files)
    except (ValueError, KeyError, TypeError):
        return _private(await make_response('File sizes are unavailable. Please index these files again.', 409))
    response = Response(None if request.method == 'HEAD' else archive(), content_type='application/zip')
    response.automatically_set_content_length = False
    response.headers.update({
        'Content-Disposition': f'attachment; filename="GateStream-{ticket["section"]}.zip"',
        'Content-Length': str(length),
        'Accept-Ranges': 'none',
        'X-Content-Type-Options': 'nosniff',
        'Referrer-Policy': 'no-referrer',
    })
    response.timeout = None
    response = _private(response)
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response
