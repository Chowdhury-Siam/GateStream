import asyncio
import time
import shutil
import psutil
from hydrogram import enums, filters
from hydrogram.types import Message
from bot.clients import TelegramBot, worker_clients, get_heartbeat_status
from bot.config import Telegram
from bot.database import db
from bot.modules.static import get_human_size
from bot.modules.decorators import verify_user, verify_admin
from bot.modules.new_users import notify_new_user
from bot.modules.personal_library import send_login_link
from bot.modules.bot_menu import send_menu
from bot.modules.access import group_id
from bot.modules.activity import activity
from bot.modules import worker_repair

_repair_task = None
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

@TelegramBot.on_message(filters.command(['start', 'help']) & (filters.private | filters.group) & filters.text)
@verify_user
async def start_command(client, msg: Message):
    is_start = msg.command[0].split('@', 1)[0].lower() == 'start'
    in_group = group_id(msg) is not None
    if not in_group and is_start and msg.command[1:] == ['library']:
        await send_login_link(msg)
    else:
        await send_menu(msg, msg.from_user.id)
    if not in_group and is_start:
        await notify_new_user(client, msg.from_user)

@TelegramBot.on_message(filters.command('stats') & filters.private)
@verify_admin
async def stats_command(_, msg: Message):
    snapshot = activity.snapshot()
    media = snapshot['phases']['media']
    connected = len(worker_repair.connected_workers())
    pings, last_ping = get_heartbeat_status()
    heartbeat = f'{pings} checks · last {get_readable_time(max(0, int(time.time() - last_ping)))} ago' if last_ping else 'Waiting for first check'
    try:
        async with asyncio.timeout(12):
            if db.db is None:
                raise RuntimeError
            uploads = await db.db['direct_files'].count_documents({})
            folders = await db.db['library_folders'].count_documents({'state': 'ready'})
            library = f'Uploads {uploads:,} · ZIP folders {folders:,}'
    except Exception:
        library = 'MongoDB library counts unavailable.'
    try:
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
        f"Sent {get_human_size(media['bytes'])} · Files completed {media['single_downloads']:,} · ZIPs completed {media['zip_downloads']:,}\n"
        f"Totals storage: {snapshot['persistence']['state']}\n\n"
        '/workers to test log posting · /workersetup to connect the repair account · /workerrepair to add missing workers'
    )
    await msg.reply(text, quote=True, parse_mode=enums.ParseMode.DISABLED)

@TelegramBot.on_message(filters.command('log') & filters.private)
@verify_user
@verify_admin
async def log_command(_, msg: Message):
    try:
        await msg.reply_document('event-log.txt', quote=True)
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
            channels = await worker_repair.storage_channels()
    except Exception:
        return None, 'Could not read the storage channels from MongoDB.'
    if channel_id not in channels:
        return None, 'Choose a channel already used by GateStream or omit the ID to use TELEGRAM_CHANNEL_ID.'
    return channel_id, ''


@TelegramBot.on_message(filters.command(['workers', 'bots']) & filters.private)
@verify_admin
async def workers_command(_, msg: Message):
    if _worker_check_lock.locked():
        return await msg.reply('A worker log check is already running.', quote=True)
    async with _worker_check_lock:
        channel_id, note = await _worker_channel(msg)
        if channel_id is None:
            return await msg.reply(note, quote=True)
        status = await msg.reply('Checking which bots can post to the log…', quote=True)
        total, failures = await worker_repair.probe_workers(channel_id)
        if not failures:
            return await status.edit_text(f'All {total} bots posted to the log.', parse_mode=enums.ParseMode.DISABLED)

        summary = f'{len(failures)} of {total} bots could not post to channel {channel_id}.'
        for start in range(0, len(failures), 30):
            text = '\n'.join([summary, *failures[start:start + 30]])
            if start == 0:
                await status.edit_text(text, parse_mode=enums.ParseMode.DISABLED)
            else:
                await msg.reply(text, quote=True, parse_mode=enums.ParseMode.DISABLED)


@TelegramBot.on_message(filters.command('workersetup') & filters.private)
@verify_admin
async def workersetup_command(_, msg: Message):
    await send_login_link(msg, worker_setup=True)


@TelegramBot.on_message(filters.command('workerrepair') & filters.private)
@verify_admin
async def workerrepair_command(_, msg: Message):
    global _repair_task
    channel_id, note = await _worker_channel(msg)
    if channel_id is None:
        return await msg.reply(note, quote=True)
    if _repair_task and not _repair_task.done():
        return await msg.reply('Worker repair is already running.', quote=True)

    async def run():
        status = await msg.reply(f'Checking channel {channel_id} before worker repair…', quote=True)

        async def report(text):
            try:
                await status.edit_text(text, parse_mode=enums.ParseMode.DISABLED)
            except Exception:
                pass

        try:
            result = await worker_repair.repair_channel(channel_id, report)
        except Exception as error:
            result = f'Worker repair could not proceed ({type(error).__name__}).'
        await report(result)

    _repair_task = asyncio.create_task(run())
