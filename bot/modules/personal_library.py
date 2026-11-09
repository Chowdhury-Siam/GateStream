"""User-owned Telegram uploads, persistent folder drafts, and browser sessions."""
import asyncio
import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from weakref import WeakValueDictionary

from bot.config import Server, Telegram
from bot.database import db
from bot.modules.archive import archive_filename
from bot.modules.access import has_access

MAX_FOLDER_FILES = 500
LOGIN_AGE = 5 * 60
SESSION_AGE = 8 * 60 * 60
COOKIE = 'gatestream_library'
SETUP_COOKIE = 'gatestream_worker_setup'
_locks = WeakValueDictionary()


def user_lock(user_id):
    # The Telegram update handlers share one process. Serialize a user's
    # uploads with their /index commands without blocking other users.
    return _locks.setdefault(user_id, asyncio.Lock())


def uploads_filter():
    sources = [{'catalog_section': 'personal'}]
    if Telegram.CHANNEL_ID:
        # Earlier private uploads were saved as Link records in the bin channel.
        sources.append({'catalog_section': {'$in': ['link', None]}, 'channel_id': Telegram.CHANNEL_ID})
    return {'$or': sources}


def _folders():
    return db.db['library_folders']


async def active_folder(user_id, chat_id=None, *, topic_id=None):
    return await _folders().find_one(
        {'user_id': user_id, 'chat_id': chat_id, 'topic_id': topic_id, 'state': 'collecting'})


def folder_name(value):
    return archive_filename(value, 'Index').removesuffix('.zip')


async def create_folder(user_id, name, chat_id=None, *, topic_id=None):
    folder = dict(_id=secrets.token_urlsafe(32), user_id=user_id, state='collecting',
                  name=name, created_at=datetime.now(timezone.utc))
    if chat_id is not None:
        folder['chat_id'] = chat_id
    if topic_id is not None:
        folder['topic_id'] = topic_id
    await _folders().insert_one(folder)
    return folder


async def finish_folder(folder, name):
    await _folders().update_one({'_id': folder['_id'], 'user_id': folder['user_id'], 'state': 'collecting'},
        {'$set': {'state': 'ready', 'name': name, 'closed_at': datetime.now(timezone.utc)}})


async def discard_empty_folder(folder):
    await _folders().delete_one({'_id': folder['_id'], 'user_id': folder['user_id'], 'state': 'collecting'})


async def folder_by_id(folder_id, user_id=None):
    if not valid_token(folder_id):
        return None
    query = {'_id': folder_id, 'state': 'ready'}
    if user_id is not None:
        query['user_id'] = user_id
    return await _folders().find_one(query)


async def rename_folder(folder_id, user_id, name):
    if not valid_token(folder_id):
        return False
    result = await _folders().update_one({'_id': folder_id, 'user_id': user_id, 'state': 'ready'},
                                        {'$set': {'name': name}})
    return bool(result.matched_count)


def _file_query(user_id, folder_id):
    return {'user_id': user_id, 'personal_index_id': folder_id, 'episodes': {'$exists': False}, **uploads_filter()}


async def folder_count(user_id, folder_id):
    return await db.direct_files.count_documents(_file_query(user_id, folder_id))


async def folder_files(user_id, folder_id):
    return await db.direct_files.find(_file_query(user_id, folder_id)).sort([
        ('message_id', 1), ('_id', 1)]).limit(MAX_FOLDER_FILES).to_list(length=MAX_FOLDER_FILES)


async def page_items(user_id, tab, query, page, page_size):
    if tab == 'single':
        collection, filters, field = db.direct_files, _file_query(user_id, None), 'file_name'
    else:
        collection, filters, field = _folders(), {'user_id': user_id, 'state': 'ready'}, 'name'
    if query:
        filters[field] = {'$regex': re.escape(query), '$options': 'i'}
    total = await collection.count_documents(filters)
    pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, pages)
    items = await collection.find(filters).sort([('created_at', -1), ('_id', 1)]).skip(
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
