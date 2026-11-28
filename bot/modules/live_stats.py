"""One editable Telegram stats dashboard per bot."""
import asyncio
import logging
import shutil
import time

import psutil
from hydrogram.enums import ParseMode
from hydrogram.errors import (
    ChannelPrivate, ChatWriteForbidden, FloodWait, MessageAuthorRequired,
    MessageEditTimeExpired, MessageIdInvalid, MessageNotModified, MsgIdInvalid,
    RPCError,
)
from pymongo.errors import PyMongoError

from bot import version
from bot.clients import get_heartbeat_status, worker_clients
from bot.database import db
from bot.modules import lifecycle, worker_diagnostics
from bot.modules.activity import activity
from bot.modules.static import get_human_size

logger = logging.getLogger('bot')
REFRESH_SECONDS = 5
METRICS_SECONDS = 30
MISSING_MESSAGE = (MessageIdInvalid, MsgIdInvalid)
EDIT_GONE = (*MISSING_MESSAGE, ChannelPrivate, ChatWriteForbidden,
             MessageAuthorRequired, MessageEditTimeExpired)
STORE_ERRORS = (PyMongoError, OSError, RuntimeError, TimeoutError)
TELEGRAM_ERRORS = (RPCError, OSError, TimeoutError)


def _uptime(seconds):
    hours, minutes = divmod(int(seconds) // 60, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f'{days}d {hours}h {minutes}m'
    return f'{hours}h {minutes}m' if hours else f'{minutes}m'


def _host_metrics():
    try:
        memory = get_human_size(psutil.Process().memory_info().rss)
        disk = get_human_size(shutil.disk_usage('.').free)
        cpu = psutil.cpu_percent(interval=None)
        return f'RAM <b>{memory}</b> · CPU <b>{cpu:.1f}%</b>\nDisk free <b>{disk}</b>'
    except (OSError, psutil.Error):
        return 'Host metrics unavailable'


def _render(uploads, host):
    snapshot = activity.snapshot()
    media = snapshot['phases']['media']
    connected = len(worker_diagnostics.connected_workers())
    total = len(worker_clients)
    status = '🟢' if total and connected == total else '🟠' if connected else '🔴'
    filled = round(8 * connected / total) if total else 0
    meter = '█' * filled + '░' * (8 - filled)
    heartbeats, last_heartbeat = get_heartbeat_status()
    heartbeat = 'Waiting for heartbeat'
    if last_heartbeat:
        age = max(0, int(time.time() - last_heartbeat))
        last_check = 'just now' if age < 60 else f'{_uptime(age)} ago'
        heartbeat = f'{heartbeats:,} heartbeat checks · {last_check}'
    upload_count = f'<b>{uploads:,}</b> uploads' if uploads is not None else 'Uploads —'
    storage = {
        'saved': 'Totals saved',
        'pending': 'Loading saved totals',
        'unavailable': 'Totals storage unavailable',
    }[snapshot['persistence']['state']]
    return (
        f'<b>◉ GateStream</b>  ·  <code>v{version}</code>\n'
        '<i>Live stats</i>\n\n'
        f'<blockquote><b>⚡ Transfers</b>  ·  <b>{get_human_size(int(media["speed_bps"]))}/s</b>\n'
        f'<b>{snapshot["active_media_links"]:,}</b> active links · <b>{media["active"]:,}</b> connections\n'
        f'<b>{get_human_size(media["bytes"])}</b> sent · <b>{media["single_downloads"]:,}</b> completed</blockquote>\n\n'
        f'<blockquote><b>🤖 Telegram</b>  ·  {status} <b>{connected}/{total}</b> online\n'
        f'<code>{meter}</code>  ·  Uptime <b>{_uptime(snapshot["uptime_seconds"])}</b>\n'
        f'{heartbeat}</blockquote>\n\n'
        f'<blockquote><b>💾 Host &amp; storage</b>\n{host}\n'
        f'{upload_count} · {storage}</blockquote>'
    )


class LiveStats:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.current: tuple[int, int] | None = None
        self.task: asyncio.Task | None = None

    @staticmethod
    def _settings(client):
        if db.db is None:
            raise RuntimeError('MongoDB is not available')
        return db.db['bot_settings'], {'_id': f'live_stats:{client.me.id}'}

    async def _load(self, client):
        collection, key = self._settings(client)
        async with asyncio.timeout(4):
            saved = await collection.find_one(key, {'chat_id': 1, 'message_id': 1})
        return (saved['chat_id'], saved['message_id']) if saved else None

    async def _save(self, client, location):
        try:
            collection, key = self._settings(client)
            async with asyncio.timeout(4):
                await collection.update_one(key, {'$set': {
                    'chat_id': location[0], 'message_id': location[1],
                }}, upsert=True)
            return True
        except STORE_ERRORS as error:
            logger.warning('Could not save the live stats message (%s).', type(error).__name__)
            return False

    async def restore(self, client):
        async with self.lock:
            if self.current is not None:
                return
            try:
                self.current = await self._load(client)
            except STORE_ERRORS as error:
                logger.warning('Could not restore the live stats message (%s).', type(error).__name__)
                return
            if self.current:
                self.task = lifecycle.spawn(self._refresh(client, self.current))

    async def show(self, client, request):
        async with self.lock:
            # Keep the current dashboard running until Telegram confirms removal.
            try:
                previous = self.current or await self._load(client)
                if previous:
                    try:
                        async with asyncio.timeout(8):
                            await client.delete_messages(*previous)
                    except MISSING_MESSAGE:
                        pass
            except STORE_ERRORS + (RPCError,) as error:
                logger.warning('Could not replace the live stats message (%s).', type(error).__name__)
                await request.reply('Could not replace the previous stats dashboard. Try /stats again.',
                                    quote=True, parse_mode=ParseMode.DISABLED)
                return

            if self.task:
                self.task.cancel()
                await asyncio.gather(self.task, return_exceptions=True)
                self.task = None
            self.current = None
            metrics = await self._metrics()
            text = _render(*metrics)
            message = await request.reply(text, quote=False, parse_mode=ParseMode.HTML, disable_notification=True)
            self.current = (message.chat.id, message.id)
            saved = await self._save(client, self.current)
            self.task = lifecycle.spawn(self._refresh(client, self.current, text, saved, metrics))

    @staticmethod
    async def _metrics():
        uploads = None
        try:
            if db.direct_files is not None:
                async with asyncio.timeout(2):
                    uploads = await db.direct_files.count_documents({}, hint='_id_')
        except STORE_ERRORS:
            pass
        return uploads, _host_metrics()

    async def _refresh(self, client, location, previous_text=None, saved=True, metrics=None):
        next_sample = time.monotonic() + METRICS_SECONDS if metrics else 0
        while True:
            now = time.monotonic()
            if now >= next_sample:
                next_sample = now + METRICS_SECONDS
                metrics = await self._metrics()
                if not saved:
                    saved = await self._save(client, location)

            text = _render(*metrics)
            delay = REFRESH_SECONDS
            if text != previous_text:
                try:
                    async with asyncio.timeout(8):
                        await client.edit_message_text(*location, text, parse_mode=ParseMode.HTML)
                    previous_text = text
                except MessageNotModified:
                    previous_text = text
                except EDIT_GONE:
                    return
                except FloodWait as error:
                    delay = max(delay, error.value)
                except TELEGRAM_ERRORS as error:
                    logger.debug('Could not refresh the stats dashboard (%s).', type(error).__name__)
                    delay = METRICS_SECONDS
            await asyncio.sleep(delay)


live_stats = LiveStats()
