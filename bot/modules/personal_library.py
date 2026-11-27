"""User-owned Telegram uploads and browser sessions."""
import asyncio
import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from weakref import WeakValueDictionary

from bot.config import Server, Telegram
from bot.database import db
from bot.modules.access import has_access

LOGIN_AGE = 5 * 60
SESSION_AGE = 8 * 60 * 60
COOKIE = 'gatestream_library'
SETUP_COOKIE = 'gatestream_worker_setup'
_locks = WeakValueDictionary()


def user_lock(user_id):
    # Serialize one user's uploads so duplicate group updates reuse their link.
    return _locks.setdefault(user_id, asyncio.Lock())


def uploads_filter():
    sources = [{'catalog_section': 'personal'}]
    if Telegram.CHANNEL_ID:
        # Earlier private uploads were saved as Link records in the bin channel.
        sources.append({'catalog_section': {'$in': ['link', None]}, 'channel_id': Telegram.CHANNEL_ID})
    return {'$or': sources}


async def page_items(user_id, query, page, page_size):
    # Include uploads from both finished and open folders created by older releases.
    filters = {'user_id': user_id, 'episodes': {'$exists': False}, **uploads_filter()}
    if query:
        filters['file_name'] = {'$regex': re.escape(query), '$options': 'i'}
    total = await db.direct_files.count_documents(filters)
    pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, pages)
    items = await db.direct_files.find(filters).sort([('created_at', -1), ('_id', 1)]).skip(
        (page - 1) * page_size).limit(page_size).to_list(length=page_size)
    return items, total, page, pages


def valid_token(token):
    return isinstance(token, str) and re.fullmatch(r'[A-Za-z0-9_-]{43}', token) is not None


def _digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _sessions():
    return db.db['library_sessions']


async def _new_token(user_id, kind, age):
    token = secrets.token_urlsafe(32)
    await _sessions().insert_one(dict(_id=_digest(token), user_id=user_id, kind=kind,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=age)))
    return token


def _token_query(token, kind):
    return {'_id': _digest(token), 'kind': kind, 'expires_at': {'$gt': datetime.now(timezone.utc)}}


async def send_login_link(message, *, user_id=None, worker_setup=False):
    from hydrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    user_id = user_id if user_id is not None else message.from_user.id
    if worker_setup:
        if user_id != Telegram.OWNER_ID:
            return
        if not Server.BASE_URL.startswith('https://'):
            return await message.reply('Worker setup requires an HTTPS BASE_URL.', quote=True)
        token = await _new_token(user_id, 'worker_setup_login', LOGIN_AGE)
        url = f'{Server.BASE_URL}/index/sign-in?setup=workers#{token}'
        text = 'Open the private setup page to connect your Telegram account for worker repair. The link expires in five minutes.'
        label = 'Open worker setup'
    else:
        token = await _new_token(user_id, 'login', LOGIN_AGE)
        url = f'{Server.BASE_URL}/index/sign-in#{token}'
        text = 'Open this one-time link in the browser where you want to use your library. It expires in five minutes.'
        label = 'Sign in to my library'
    await message.reply(text, quote=True, disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(label, url=url)]]))


async def exchange_login(token, *, worker_setup=False):
    if not valid_token(token):
        return None
    kind = 'worker_setup_login' if worker_setup else 'login'
    record = await _sessions().find_one_and_delete(_token_query(token, kind))
    if record:
        user_id = record['user_id']
        if worker_setup:
            if user_id == Telegram.OWNER_ID:
                return await _new_token(user_id, 'worker_setup', 2 * LOGIN_AGE)
        elif await has_access(user_id):
            return await _new_token(user_id, 'session', SESSION_AGE)
    return None


async def session_user(token, *, worker_setup=False):
    if not valid_token(token):
        return None
    kind = 'worker_setup' if worker_setup else 'session'
    record = await _sessions().find_one(_token_query(token, kind))
    if record:
        user_id = record['user_id']
        if worker_setup:
            return user_id if user_id == Telegram.OWNER_ID else None
        if await has_access(user_id):
            return user_id
    return None


async def end_session(token, *, worker_setup=False):
    if valid_token(token):
        kind = 'worker_setup' if worker_setup else 'session'
        await _sessions().delete_one({'_id': _digest(token), 'kind': kind})
