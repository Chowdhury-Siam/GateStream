"""Personal upload listings and explicit ZIP sharing."""
import asyncio
import hmac
import secrets

from quart import Blueprint, make_response, redirect, render_template, request, url_for

from bot.config import Server, Telegram
from bot.modules import personal_library as library
from bot.modules.archive import archive_filename
from bot.modules.static import get_human_size
from .catalog import _private, create_archive
from .error import abort

bp = Blueprint('personal_library', __name__)
PAGE_SIZE = 30
CSRF_COOKIE = 'gatestream_index_csrf'


@bp.after_request
async def private_headers(response):
    _private(response)
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


def _secure():
    return request.is_secure or Server.BASE_URL.startswith('https://')


def _csrf_token():
    value = request.cookies.get(CSRF_COOKIE, '')
    return value if 20 <= len(value) <= 128 else secrets.token_urlsafe(32)


def _csrf_valid(form):
    cookie = request.cookies.get(CSRF_COOKIE, '')
    value = form.get('csrf', '')
    return bool(cookie and len(cookie) <= 128 and len(value) <= 128 and hmac.compare_digest(cookie.encode(), value.encode()))


def _csrf_cookie(response, token):
    response.set_cookie(CSRF_COOKIE, token, max_age=library.SESSION_AGE,
                        httponly=True, secure=_secure(), samesite='Lax', path='/')


async def page(status=200, **context):
    csrf = _csrf_token()
    response = await make_response(await render_template('personal_library.html', csrf=csrf,
        bot_username=Telegram.BOT_USERNAME, get_human_size=get_human_size, **context), status)
    _csrf_cookie(response, csrf)
    return response


async def viewer():
    return await library.session_user(request.cookies.get(library.COOKIE))


async def form_data():
    request.max_content_length = 4096
    form = await request.form
    if not _csrf_valid(form):
        abort(400, 'The form expired. Reload the page and try again.')
    return form


@bp.route('/index')
async def index():
    user_id = await viewer()
    if user_id is None:
        return await page()
    tab = 'single' if request.args.get('tab') == 'single' else 'index'
    query = request.args.get('q', '').strip()[:100]
    raw_page = request.args.get('page', '')
    number = max(1, int(raw_page)) if raw_page.isascii() and raw_page.isdigit() and len(raw_page) <= 8 else 1
    items, total, number, pages = await library.page_items(user_id, tab, query, number, PAGE_SIZE)
    return await page(signed_in=True, tab=tab, items=items, total=total, query=query,
                      number=number, pages=pages)


@bp.route('/index/sign-in', methods=['GET', 'POST'])
async def sign_in():
    if request.method == 'POST':
        token = await library.exchange_login((await form_data()).get('token'))
        if token is None:
            return await page(400, error='That sign-in link expired or was already used. Ask the bot for a new link.')
        response = redirect(url_for('.index'), code=303)
        response.set_cookie(library.COOKIE, token, max_age=library.SESSION_AGE, httponly=True,
                            secure=_secure(), samesite='Lax', path='/index')
        return response
    return await page(confirm_sign_in=True)


@bp.post('/index/sign-out')
async def sign_out():
    await form_data()
    await library.end_session(request.cookies.get(library.COOKIE))
    response = redirect(url_for('.index'), code=303)
    response.delete_cookie(library.COOKIE, path='/index')
    return response


@bp.route('/index/folder/<string:folder_id>')
async def folder_detail(folder_id):
    user_id = await viewer()
    if user_id is None:
        return redirect(url_for('.index'))
    folder = await library.folder_by_id(folder_id, user_id)
    if folder is None:
        abort(404, 'Folder not found.')
    files = await library.folder_files(user_id, folder_id)
    return await page(signed_in=True, tab='index', folder=folder, files=files)


@bp.post('/index/folder/<string:folder_id>/rename')
async def rename_folder(folder_id):
    user_id = await viewer()
    if user_id is None:
        abort(401, 'Sign in to rename a folder.')
    form = await form_data()
    try:
        name = library.folder_name(form.get('name', ''))
    except ValueError as error:
        abort(400, str(error))
    if not await library.rename_folder(folder_id, user_id, name):
        abort(404, 'Folder not found.')
    return redirect(url_for('.folder_detail', folder_id=folder_id), code=303)


@bp.route('/index/share/<string:folder_id>', methods=['GET', 'POST'])
async def share_folder(folder_id):
    folder = await library.folder_by_id(folder_id)
    if folder is None:
        abort(404, 'Shared folder not found.')
    if request.method != 'POST':
        count = await library.folder_count(folder['user_id'], folder_id)
        return await page(shared=folder, total=count)
    await form_data()
    files = []
    try:
        async with asyncio.timeout(25):
            files = await library.folder_files(folder['user_id'], folder_id)
            if not files:
                return await page(410, shared=folder, total=0, error='This folder has no downloadable files.')
            filename = archive_filename(folder['name'])
            token = await create_archive(files, filename,
                                         folder_id=folder_id, user_id=folder['user_id'])
    except ValueError as error:
        return await page(413, shared=folder, total=len(files), error=str(error))
    except TimeoutError:
        abort(503, 'The server could not create the ZIP link in time. Please try again.')
    return redirect(url_for('catalog.download_archive', token=token, filename=filename), code=303)
