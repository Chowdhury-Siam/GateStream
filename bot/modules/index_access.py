"""Persistent shared-password access for the media index."""
import asyncio
import secrets

from itsdangerous import BadSignature, URLSafeTimedSerializer
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from werkzeug.security import check_password_hash, generate_password_hash

from bot.database import db

COOKIE = 'gatestream_index'
SESSION_AGE = 8 * 60 * 60
_hash_slots = asyncio.Semaphore(2)


def _collection():
    if db.db is None:
        raise RuntimeError('Database is unavailable')
    return db.db['settings']


def _record(password):
    return dict(password_hash=generate_password_hash(password), session_secret=secrets.token_hex(32))


async def settings():
    collection = _collection()
    document = await collection.find_one({'_id': 'index_access'})
    if document is not None:
        return document
    async with _hash_slots:
        initial = await asyncio.to_thread(_record, 'Siam')
    try:
        return await collection.find_one_and_update({'_id': 'index_access'}, {'$setOnInsert': initial},
            upsert=True, return_document=ReturnDocument.AFTER)
    except DuplicateKeyError:
        return await collection.find_one({'_id': 'index_access'})


async def set_password(password):
    if not 1 <= len(password) <= 256:
        raise ValueError('Password must contain 1 to 256 characters.')
    async with _hash_slots:
        values = await asyncio.to_thread(_record, password)
    await _collection().update_one({'_id': 'index_access'}, {'$set': values}, upsert=True)


async def verify_password(password, document):
    if not isinstance(password, str) or not 1 <= len(password) <= 256:
        return False
    async with _hash_slots:
        return await asyncio.to_thread(check_password_hash, document['password_hash'], password)


def session_token(document):
    return URLSafeTimedSerializer(document['session_secret'], salt='gatestream-index').dumps({'catalog': True})


def valid_session(token, document):
    if not token or not document or len(token) > 1024:
        return False
    try:
        value = URLSafeTimedSerializer(document['session_secret'], salt='gatestream-index').loads(token, max_age=SESSION_AGE)
        return value == {'catalog': True}
    except (BadSignature, ValueError, TypeError):
        return False
