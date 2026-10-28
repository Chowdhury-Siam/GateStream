import hmac
import logging
import secrets
import time
from collections import deque

from quart import Blueprint, make_response, redirect, render_template, request, url_for

from bot.config import Server
from bot.database.catalog import CATEGORIES, PAGE_SIZE, list_files
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
    response = await make_response(await render_template('catalog.html', unlocked=False, csrf=csrf, error=error), status)
    _csrf_cookie(response, csrf)
    return _private(response)


@bp.route('/index')
async def index_page():
    if not await authenticated():
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
        data = await list_files(page, query, category)
    except Exception as error:
        logger.error('Index listing unavailable (%s).', type(error).__name__)
        return _private(await make_response('The media index is temporarily unavailable. Please try again.', 503))
    if page > data['pages']:
        return _private(redirect(url_for('catalog.index_page', q=query, category=category, page=data['pages'])))
    for item in data['files']:
        try:
            item['file_size'] = max(0, int(item.get('file_size') or 0))
        except (ValueError, TypeError):
            item['file_size'] = 0
    csrf = _csrf_token()
    response = await make_response(await render_template('catalog.html', unlocked=True, csrf=csrf,
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
    response = redirect(url_for('catalog.index_page'))
    response.set_cookie(index_access.COOKIE, index_access.session_token(document),
        max_age=index_access.SESSION_AGE, httponly=True, secure=_secure(), samesite='Lax', path='/')
    return _private(response)


@bp.route('/index/logout', methods=['POST'])
async def index_logout():
    request.max_content_length = 4096
    if not _csrf_valid(await request.form):
        return await _login('The sign-out form expired. Please try again.', 400)
    response = redirect(url_for('catalog.index_page'))
    response.delete_cookie(index_access.COOKIE, path='/')
    return _private(response)
