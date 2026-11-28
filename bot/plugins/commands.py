from hydrogram import Client
import asyncio
from hydrogram import enums, filters
from hydrogram.types import Message
from bot.config import Telegram
from bot.modules.decorators import verify_user, verify_admin
from bot.modules.new_users import notify_new_user
from bot.modules.bot_menu import send_menu
from bot.modules.access import group_id, is_owner
from bot.modules import worker_diagnostics
from bot.modules.live_stats import live_stats
from bot.config import Resources

_worker_check_lock = asyncio.Lock()

@Client.on_message(filters.command('start') & (filters.private | filters.group) & filters.text)
@verify_user
async def start_command(client, msg: Message):
    await send_menu(msg, msg.from_user.id)
    if group_id(msg) is None:
        await notify_new_user(client, msg.from_user)


@Client.on_message(filters.command('help') & (filters.private | filters.group) & filters.text)
@verify_user
async def help_command(_, msg: Message):
    in_group = group_id(msg) is not None
    lines = ['GateStream help', '', '/start — Open the welcome menu.']
    if in_group:
        lines += [
            'Post a file in the configured File To Link topic to get a link automatically.',
            '/link — Reply to your own file in that topic to retry.',
        ]
    else:
        lines.append('Send or forward a file to this chat to get a download link.')
    lines.append('Use Revoke link on your file reply to invalidate that link.')
    if is_owner(msg.from_user.id):
        lines += ['', 'Owner commands', '/auth and /unauth — Manage user or group access.',
                  '/filetolink — Set the File To Link topic from inside that topic.',
                  '/stats — Move the live dashboard to this chat or topic.',
                  '/purge — Delete a range of chat messages.']
        if not in_group:
            lines += [
                '/log and /workers — Check service health and worker posting.',
                '/set_title, /revoke, /sync_duration — Manage saved files.',
                '/backup and /restore — Export or import file records.',
                '/clean and /restart — Maintain the service.',
            ]
    await msg.reply('\n'.join(lines), quote=True, parse_mode=enums.ParseMode.DISABLED)


@Client.on_message(filters.command('stats') & (filters.private | filters.group) & filters.text)
@verify_admin
async def stats_command(client, msg: Message):
    await live_stats.show(client, msg)

@Client.on_message(filters.command('log') & filters.private)
@verify_user
@verify_admin
async def log_command(_, msg: Message):
    try:
        await msg.reply_document(str(Resources.LOG_FILE), quote=True)
    except Exception as e:
        await msg.reply(f"❌ Failed to send log file: `{e}`", quote=True)

async def _worker_channel(msg):
    args = msg.command[1:]
    if not args:
        if Telegram.CHANNEL_ID < 0:
            return Telegram.CHANNEL_ID, ''
        return None, 'Set TELEGRAM_CHANNEL_ID or provide a GateStream storage channel ID.'
    try:
        if len(args) != 1:
            raise ValueError
        channel_id = int(args[0])
    except ValueError:
        return None, 'Use a numeric Telegram channel ID, such as -1001234567890.'
    if channel_id == Telegram.CHANNEL_ID and channel_id < 0:
        return channel_id, ''
    try:
        async with asyncio.timeout(10):
            channels = await worker_diagnostics.storage_channels()
    except Exception:
        return None, 'Could not read the storage channels from MongoDB.'
    if channel_id not in channels:
        return None, 'Choose a channel already used by GateStream or omit the ID to use TELEGRAM_CHANNEL_ID.'
    return channel_id, ''


@Client.on_message(filters.command(['workers', 'bots']) & filters.private)
@verify_admin
async def workers_command(_, msg: Message):
    if _worker_check_lock.locked():
        return await msg.reply('A worker log check is already running.', quote=True)
    async with _worker_check_lock:
        channel_id, note = await _worker_channel(msg)
        if channel_id is None:
            return await msg.reply(note, quote=True)
        status = await msg.reply('Checking which bots can post to the log…', quote=True)
        total, failures = await worker_diagnostics.probe_workers(channel_id)
        if not failures:
            return await status.edit_text(f'All {total} bots posted to the log.', parse_mode=enums.ParseMode.DISABLED)

        summary = f'{len(failures)} of {total} bots could not post to channel {channel_id}.'
        for start in range(0, len(failures), 30):
            text = '\n'.join([summary, *failures[start:start + 30]])
            if start == 0:
                await status.edit_text(text, parse_mode=enums.ParseMode.DISABLED)
            else:
                await msg.reply(text, quote=True, parse_mode=enums.ParseMode.DISABLED)


