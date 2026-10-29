import asyncio
import logging
import re
from hydrogram import filters, enums
from hydrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from bot.clients import TelegramBot
from bot.config import Telegram, Server
from bot.database import db
from bot.modules.decorators import verify_user
from bot.modules.parser import parse_telegram_link
from bot.modules.channel_index import scan_channel

logger = logging.getLogger('bot')
# ponytail: one scan per process; use a database lease if running multiple bot processes.
_task = None
_stop = asyncio.Event()


def parse_target(value):
    parsed = parse_telegram_link(value)
    if parsed:
        return parsed
    if re.fullmatch(r'-100\d+', value):
        return int(value), None
    match = re.fullmatch(r'(?:https?://t\.me/)?@?([A-Za-z][A-Za-z0-9_]{3,})/?', value)
    if match:
        return match[1], None
    raise ValueError('Use a channel username, -100 channel ID, or the last post link.')


@TelegramBot.on_message(filters.command(['index', 'catalog']) & filters.private)
@verify_user
async def index_command(client, message):
    global _task
    parts = (message.text or '').split()
    reply = message.reply_to_message
    forwarded = reply and reply.forward_from_chat and reply.forward_from_message_id
    if (len(parts) < 2 and not forwarded) or parts[0].split('@')[0].lower() == '/catalog':
        return await message.reply(
            'Open public Index files or protected Link files.\n\n'
            'Admins: /index LAST_POST_LINK scans a channel automatically from the beginning. '
            'Later use /index @channel to scan newly observed posts. '
            'Use /index LAST_POST_LINK rescan to recheck older files, and /cancelindex to stop.',
            quote=True, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Open catalog', url=f'{Server.BASE_URL}/index')]]))
    if not message.from_user or message.from_user.id not in Telegram.ADMIN_IDS:
        return await message.reply('Only the owner and authorized admins can index channels.', quote=True)
    if _task and not _task.done():
        return await message.reply('A channel scan is already running. Use /cancelindex before starting another.', quote=True)
    try:
        if len(parts) >= 2:
            target, end_id = parse_target(parts[1])
        else:
            target, end_id = reply.forward_from_chat.id, reply.forward_from_message_id
        chat = await client.get_chat(target)
        if chat.type not in (enums.ChatType.CHANNEL, enums.ChatType.SUPERGROUP):
            return await message.reply('Choose a Telegram channel or supergroup.', quote=True)
        state = await db.db['index_channels'].find_one({'_id': chat.id}) or {}
        if end_id is None:
            end_id = state.get('latest_seen')
        if not end_id:
            return await message.reply('For the first scan, send /index followed by the link to the channel’s last post, or reply to a forwarded last post with /index. Telegram bots cannot discover the last historical post themselves.', quote=True)
        if not 1 <= int(end_id) < 2**31:
            raise ValueError('Invalid last message ID.')
        await db.db['index_channels'].update_one({'_id': chat.id}, {'$max': {'latest_seen': int(end_id)}}, upsert=True)
    except Exception as error:
        logger.error('Channel index setup failed (%s).', type(error).__name__)
        return await message.reply('Could not open that channel. Add the bot as an admin, then send /index LAST_POST_LINK. Private invite links cannot identify a message range.', quote=True)
    status = await message.reply('Indexing started. Files will appear in the public Index tab as they are processed.', quote=True)
    _stop.clear()

    async def report(last, end, counts, unavailable):
        text = (f'Channel indexing: {last}/{end} message IDs checked\n'
            f'Added: {counts["added"]} · Updated: {counts["updated"]} · Existing: {counts["existing"]}\n'
            f'Skipped/non-media: {counts["skipped"]} · Unclassified/non-video: {counts["unmatched"]}\n'
            'Use /cancelindex to stop. Run the command again to resume.')
        if unavailable: text += '\nMetadata unavailable/rate limited: ' + ', '.join(sorted(unavailable))
        try: await status.edit_text(text)
        except Exception: pass

    async def run():
        try:
            last, counts = await scan_channel(client, chat.id, int(end_id), message.from_user.id, _stop, report, rescan='rescan' in parts[2:])
            await status.edit_text(f'{"Stopped" if _stop.is_set() else "Index complete"} at message {last}/{end_id}.\n'
                f'Added: {counts["added"]} · Updated: {counts["updated"]} · Existing: {counts["existing"]}\n'
                f'Skipped: {counts["skipped"]} · Unclassified/non-video: {counts["unmatched"]}\n'
                f'Public catalog: {Server.BASE_URL}/index\nRun /index LAST_POST_LINK rescan to retry unclassified titles after adding API credentials.')
        except Exception as error:
            logger.error('Channel indexing stopped (%s).', type(error).__name__)
            try: await status.edit_text('Indexing stopped because Telegram, metadata, or the database was unavailable. Saved progress is retained. Run /index again with the same channel/post link to resume.')
            except Exception: pass
    _task = asyncio.create_task(run())


@TelegramBot.on_message(filters.command('cancelindex') & filters.private)
@verify_user
async def cancel_index_command(_, message):
    if not message.from_user or message.from_user.id not in Telegram.ADMIN_IDS:
        return await message.reply('Only the owner and authorized admins can stop a scan.', quote=True)
    _stop.set()
    await message.reply('Stop requested. The current file will finish, and progress will be retained.', quote=True)


@TelegramBot.on_message(filters.channel, group=99)
async def observe_channel_post(_, message):
    try:
        if db.db is not None:
            await db.db['index_channels'].update_one({'_id': message.chat.id}, {'$max': {'latest_seen': message.id}}, upsert=True)
    except Exception as error:
        logger.warning('Latest channel post could not be recorded (%s).', type(error).__name__)
