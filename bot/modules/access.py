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
