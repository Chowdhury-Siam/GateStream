import asyncio
import logging
import re
from hydrogram import filters, enums
from bot.clients import TelegramBot
from bot.config import Telegram
from bot.database import db
from bot.modules.decorators import verify_user
from bot.modules.parser import parse_telegram_link
from bot.modules.channel_index import scan_channel
from bot.modules import personal_library as library
from bot.modules.index_zip import zip_choices

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


@TelegramBot.on_message(filters.command(['index', 'catalog', 'channelindex']) & filters.private & filters.text)
@verify_user
async def index_command(client, message):
    global _task
    parts = message.text.split()
    command = parts[0].split('@')[0].lower()
    if command == '/catalog':
        return await library.send_login_link(message)
    reply = message.reply_to_message
    forwarded = reply and reply.forward_from_chat and reply.forward_from_message_id
    if command == '/index':
        # Keep the existing admin channel command when given an explicit target.
        target = parts[1] if len(parts) > 1 else ''
        channel_target = target.startswith(('https://t.me/', 'http://t.me/', '@', '-100'))
        if not channel_target:
            return await personal_index(message, message.text.split(maxsplit=1)[1] if target else '')
    if len(parts) < 2 and not forwarded:
        return await message.reply('Use /channelindex LAST_POST_LINK to scan a Telegram channel.', quote=True)
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
        if chat.id == Telegram.CHANNEL_ID:
            return await message.reply('The storage channel is reserved for personal uploads. Choose another channel.', quote=True)
        state = await db.db['index_channels'].find_one({'_id': chat.id}) or {}
        if end_id is None:
            end_id = state.get('latest_seen')
        if not end_id:
            return await message.reply('For the first scan, send /channelindex followed by the link to the channel’s last post, or reply to a forwarded last post with /channelindex. Telegram bots cannot discover the last historical post themselves.', quote=True)
        if not 1 <= int(end_id) < 2**31:
            raise ValueError('Invalid last message ID.')
        await db.db['index_channels'].update_one({'_id': chat.id}, {'$max': {'latest_seen': int(end_id)}}, upsert=True)
    except Exception as error:
        logger.error('Channel index setup failed (%s).', type(error).__name__)
        return await message.reply('Could not open that channel. Add the bot as an admin, then send /channelindex LAST_POST_LINK. Private invite links cannot identify a message range.', quote=True)
    status = await message.reply('Indexing started. Use /search in the bot to find indexed files.', quote=True)
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
                'Run /channelindex LAST_POST_LINK rescan to retry unclassified titles after adding API credentials.')
        except Exception as error:
            logger.error('Channel indexing stopped (%s).', type(error).__name__)
            try: await status.edit_text('Indexing stopped because Telegram, metadata, or the database was unavailable. Saved progress is retained. Run /channelindex again with the same channel/post link to resume.')
            except Exception: pass
    _task = asyncio.create_task(run())


async def personal_index(message, requested_name):
    if not Telegram.CHANNEL_ID:
        return await message.reply('The bot needs a storage channel before it can collect uploads.', quote=True)
    user_id = message.from_user.id
    share_id = None
    try:
        name = library.folder_name(requested_name) if requested_name else None
        async with library.user_lock(user_id):
            folder = await library.active_folder(user_id)
            if folder is None:
                await library.create_folder(user_id, name or library.folder_name(''))
                text = (f'Index started. Send up to {library.MAX_FOLDER_FILES} files, then send /index again. '
                        'Use /index My folder name to finish with a custom name.')
            else:
                count = await library.folder_count(user_id, folder['_id'])
                if not count:
                    await library.discard_empty_folder(folder)
                    text = 'Index closed. No files were added.'
                else:
                    name = name or folder['name']
                    await library.finish_folder(folder, name)
                    share_id = folder['_id']
                    text = (f'Saved {count} files in “{name}”. Choose how to download the ZIP. '
                            'Anyone with the download link can use it.')
    except ValueError as error:
        return await message.reply(str(error), quote=True, parse_mode=enums.ParseMode.DISABLED)
    except Exception as error:
        logger.error('Personal index update failed (%s).', type(error).__name__)
        return await message.reply('The index could not be updated. Please try /index again shortly.', quote=True)
    buttons = zip_choices(share_id) if share_id else None
    await message.reply(text, quote=True, parse_mode=enums.ParseMode.DISABLED, reply_markup=buttons,
                        disable_web_page_preview=True)


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
