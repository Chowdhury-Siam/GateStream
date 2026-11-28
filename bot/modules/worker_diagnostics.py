"""Check whether the configured bots can post in a storage channel."""
import asyncio

from hydrogram import raw
from hydrogram.errors import (
    ChannelPrivate, ChatAdminRequired, ChatWriteForbidden, FloodWait, PeerIdInvalid,
    UserBannedInChannel,
)

from bot.clients import TelegramBot, worker_clients
from bot.config import Telegram
from bot.database import db


async def storage_channels():
    ids = {Telegram.CHANNEL_ID}
    for collection in (db.movies, db.anime, db.webseries, db.direct_files):
        if collection is not None:
            ids.update(await collection.distinct('channel_id'))
    return sorted(cid for cid in ids if type(cid) is int and cid < 0)


def connected_workers():
    return [worker.me for worker in worker_clients if worker.is_connected and worker.me]


async def probe_workers(channel_id):
    """Send one silent log message from each connected client and return failures."""
    workers = tuple(worker_clients) or (TelegramBot,)
    slots = asyncio.Semaphore(5)

    async def probe(index, worker):
        label = 'Primary' if worker is TelegramBot else f'Worker {index}'
        if worker.me and worker.me.username:
            label += f' @{worker.me.username}'
        if not worker.is_connected:
            return f'• {label} · Offline'
        async with slots:
            try:
                async with asyncio.timeout(10):
                    peer = await worker.resolve_peer(channel_id)
                    await worker.invoke(raw.functions.messages.SendMessage(
                        peer=peer, message=f'GateStream worker check · {label}',
                        random_id=worker.rnd_id(), silent=True,
                    ), retries=1, sleep_threshold=0)
                return None
            except (ChatAdminRequired, ChatWriteForbidden):
                reason = 'Posting permission denied'
            except (ChannelPrivate, PeerIdInvalid, UserBannedInChannel):
                reason = 'Channel inaccessible'
            except FloodWait as error:
                reason = f'Rate limited for {error.value}s'
            except TimeoutError:
                reason = 'Check timed out'
            except Exception as error:
                reason = f'Post failed ({type(error).__name__})'
        return f'• {label} · {reason}'

    results = await asyncio.gather(*(probe(index, worker) for index, worker in enumerate(workers)))
    return len(workers), [failure for failure in results if failure is not None]
