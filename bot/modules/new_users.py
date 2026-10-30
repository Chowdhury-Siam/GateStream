"""Persist first starts and report Telegram-provided profile details once."""
import asyncio
from datetime import datetime, timedelta, timezone
from logging import getLogger
import unicodedata

from hydrogram.enums import ParseMode
from bot.config import Telegram
from bot.database import db

logger = getLogger('bot')


def user_notice(user, first_seen):
    def value(field):
        text = str(getattr(user, field, None) or 'Not provided')
        return ''.join(c for c in text if not unicodedata.category(c).startswith('C'))[:256]

    def flag(field):
        return 'Yes' if getattr(user, field, False) else 'No'

    username = getattr(user, 'username', None)
    profile = f'https://t.me/{username}' if username else f'tg://user?id={user.id}'
    return (
        '👤 GateStream · New user\n\n'
        f'User ID: {user.id}\n'
        f'First name: {value("first_name")}\n'
        f'Last name: {value("last_name")}\n'
        f'Username: {"@" + username if username else "Not set"}\n'
        f'Profile: {profile}\n'
        f'Language: {value("language_code")}\n'
        f'Premium: {flag("is_premium")}\n'
        f'Verified: {flag("is_verified")}\n'
        f'Bot: {flag("is_bot")}\n'
        f'Restricted: {flag("is_restricted")}\n'
        f'Scam flag: {flag("is_scam")}\n'
        f'Fake flag: {flag("is_fake")}\n'
        f'Deleted account: {flag("is_deleted")}\n'
        f'Telegram DC: {value("dc_id")}\n'
        f'First start (UTC): {first_seen:%Y-%m-%d %H:%M:%S}'
    )


async def notify_new_user(client, user):
    if not Telegram.CHANNEL_ID or user is None or db.db is None:
        return
    now = datetime.now(timezone.utc)
    until = now + timedelta(minutes=2)
    claim = {'_id': user.id, 'notify_until': until}
    collection = db.db['bot_users']
    try:
        # MongoDB's unique _id survives restarts. The lease also prevents parallel
        # /start handlers from sending the same notice and expires after a crash.
        await collection.update_one({'_id': user.id}, {
            '$setOnInsert': {'first_seen': now},
        }, upsert=True)
        record = await collection.find_one_and_update({
            '_id': user.id, 'notified_at': {'$exists': False},
            '$or': [{'notify_until': {'$exists': False}}, {'notify_until': {'$lte': now}}],
        }, {'$set': {'notify_until': until}})
        if record is None:
            return
        try:
            await asyncio.wait_for(client.send_message(
                chat_id=Telegram.CHANNEL_ID,
                text=user_notice(user, record['first_seen']),
                parse_mode=ParseMode.DISABLED,
                disable_web_page_preview=True,
            ), timeout=30)
        except Exception:
            await collection.update_one(claim, {'$unset': {'notify_until': ''}})
            raise
        await collection.update_one(claim, {
            '$set': {'notified_at': datetime.now(timezone.utc)},
            '$unset': {'notify_until': ''},
        })
    except Exception as exc:
        # Do not interrupt the welcome or log personal profile data on failure.
        logger.warning('New-user channel notice failed (%s); retry on next /start.', type(exc).__name__)
