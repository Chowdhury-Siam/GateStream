"""Owner-managed Telegram access, shared by bot handlers and browser sessions."""
import logging

from hydrogram.enums import ChatType

from bot.config import Telegram
from bot.database import db

logger = logging.getLogger('bot')
GROUP_TYPES = (ChatType.GROUP, ChatType.SUPERGROUP)


def group_id(message):
    chat = getattr(message, 'chat', None)
    return chat.id if getattr(chat, 'type', None) in GROUP_TYPES else None


def forum_topic(message):
    chat_id = group_id(message)
    topic_id = getattr(message, 'message_thread_id', None)
    if chat_id is not None and type(topic_id) is int and topic_id > 1:
        return {'chat_id': chat_id, 'topic_id': topic_id}
    return None


async def file_topic_status(message):
    topic = forum_topic(message)
    if topic is None:
        return 'no_topic'
    try:
        saved = await db.db['bot_settings'].find_one({'_id': 'file_to_link'}, {'chat_id': 1, 'topic_id': 1})
    except Exception as error:
        logger.warning('File To Link topic lookup failed (%s).', type(error).__name__)
        return 'unavailable'
    if not saved:
        return 'unbound'
    if saved.get('chat_id') == topic['chat_id'] and saved.get('topic_id') == topic['topic_id']:
        return 'active'
    return 'different'


async def is_file_topic(message):
    return await file_topic_status(message) == 'active'


async def set_file_topic(topic):
    await db.db['bot_settings'].update_one({'_id': 'file_to_link'}, {'$set': topic}, upsert=True)


def is_owner(user_id):
    return isinstance(user_id, int) and user_id > 0 and user_id == Telegram.OWNER_ID


async def has_access(user_id, chat_id=None):
    if not isinstance(user_id, int) or user_id <= 0:
        return False
    if is_owner(user_id):
        return True
    ids = [user_id, chat_id] if chat_id is not None else [user_id]
    try:
        return await db.db['bot_access'].find_one({'_id': {'$in': ids}}, {'_id': 1}) is not None
    except Exception as error:
        logger.warning('Telegram access lookup failed (%s).', type(error).__name__)
        return False


async def set_access(target_id, granted):
    collection = db.db['bot_access']
    if granted:
        await collection.update_one({'_id': target_id}, {'$set': {'kind': 'group' if target_id < 0 else 'user'}}, upsert=True)
    else:
        await collection.delete_one({'_id': target_id})
