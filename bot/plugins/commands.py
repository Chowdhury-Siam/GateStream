from hydrogram import Client
import asyncio
import time
import shutil
from hydrogram import enums, filters
from hydrogram.types import Message
from bot.clients import worker_clients, get_heartbeat_status
from bot.config import Telegram
from bot.database import db
from bot.modules.static import get_human_size
from bot.modules.decorators import verify_user, verify_admin
from bot.modules.new_users import notify_new_user
from bot.modules.bot_menu import send_menu
from bot.modules.access import group_id, is_owner
from bot.modules.activity import activity
from bot.modules import worker_diagnostics
from bot.config import Resources

_worker_check_lock = asyncio.Lock()

def get_readable_time(seconds: int) -> str:
    result = []
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    
    if days > 0:
        result.append(f"{days}d")
    if hours > 0:
        result.append(f"{hours}h")
    if minutes > 0:
        result.append(f"{minutes}m")
    result.append(f"{seconds}s")
    
    return " ".join(result) or "0s"

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
                  '/purge — Delete a range of chat messages.']
        if not in_group:
            lines += [
                '/stats, /log, /workers — Check service health and worker posting.',
                '/set_title, /revoke, /sync_duration — Manage saved files.',
                '/backup and /restore — Export or import file records.',
                '/clean and /restart — Maintain the service.',
            ]
    await msg.reply('\n'.join(lines), quote=True, parse_mode=enums.ParseMode.DISABLED)


@Client.on_message(filters.command('stats') & filters.private)
@verify_admin
async def stats_command(_, msg: Message):
    snapshot = activity.snapshot()
    media = snapshot['phases']['media']
    connected = len(worker_diagnostics.connected_workers())
    pings, last_ping = get_heartbeat_status()
    heartbeat = f'{pings} checks · last {get_readable_time(max(0, int(time.time() - last_ping)))} ago' if last_ping else 'Waiting for first check'
    try:
        async with asyncio.timeout(12):
            if db.db is None:
                raise RuntimeError
            uploads = await db.db['direct_files'].count_documents({})
            library = f'Uploads {uploads:,}'
    except Exception:
        library = 'MongoDB library counts unavailable.'
    try:
        import psutil

        rss = get_human_size(psutil.Process().memory_info().rss)
        disk = get_human_size(shutil.disk_usage('.').free)
        cpu = await asyncio.to_thread(psutil.cpu_percent, 0.1)
        host = f'Bot RAM {rss} · Host CPU {cpu:.1f}% · Disk free {disk}'
    except Exception:
        host = 'Host resource metrics unavailable.'
    text = (
        'GateStream · Live stats\n\n'
        f"Uptime {get_readable_time(int(snapshot['uptime_seconds']))} · Bots connected {connected}/{len(worker_clients)}\n"
        f'Heartbeat {heartbeat}\n{host}\n\n'
        f'Library · MongoDB\n{library}\n\n'
        f"Transfers\nActive links {snapshot['active_media_links']} · Connections {media['active']} · {get_human_size(int(media['speed_bps']))}/s\n"
        f"Sent {get_human_size(media['bytes'])} · Files completed {media['single_downloads']:,}\n"
        f"Totals storage: {snapshot['persistence']['state']}\n\n"
        '/workers to test log posting'
    )
    await msg.reply(text, quote=True, parse_mode=enums.ParseMode.DISABLED)

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


