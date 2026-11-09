"""Inspect storage-channel membership and repair it with the owner's user session."""
import asyncio
import math
import time

from cryptography.exceptions import InvalidTag
from hydrogram import Client, raw
from hydrogram.enums import ChatMemberStatus, ChatType
from hydrogram.errors import (
    ChannelPrivate, ChatAdminRequired, ChatWriteForbidden, FloodWait, PeerFlood, PeerIdInvalid,
    UserBannedInChannel, UserNotParticipant,
)

from bot.clients import TelegramBot, worker_clients
from bot.config import Telegram
from bot.database import db
from bot.modules.worker_setup import repair_credentials

ADD_INTERVAL = 60
CHANNEL_TYPES = (ChatType.CHANNEL, ChatType.SUPERGROUP)


class RepairError(Exception):
    pass


async def storage_channels():
    ids = {Telegram.CHANNEL_ID}
    for collection in (db.movies, db.anime, db.webseries, db.direct_files):
        if collection is not None:
            ids.update(await collection.distinct('channel_id'))
    return sorted(cid for cid in ids if type(cid) is int and cid < 0)


def connected_workers():
    return [worker.me for worker in worker_clients if worker.is_connected and worker.me]


async def membership(client, channel_id, user_id, *, expected_id=None):
    channel = await client.resolve_peer(channel_id)
    if not isinstance(channel, raw.types.InputPeerChannel):
        return 'unverified'
    peer = await client.resolve_peer(user_id)
    if expected_id is not None and getattr(peer, 'user_id', None) != expected_id:
        return 'unverified'
    try:
        member = (await client.invoke(raw.functions.channels.GetParticipant(
            channel=raw.types.InputChannel(channel_id=channel.channel_id, access_hash=channel.access_hash),
            participant=peer,
        ), sleep_threshold=0)).participant
    except UserNotParticipant:
        return 'missing'
    if isinstance(member, (raw.types.ChannelParticipantCreator, raw.types.ChannelParticipantAdmin,
                           raw.types.ChannelParticipant, raw.types.ChannelParticipantSelf)):
        return 'present'
    if isinstance(member, raw.types.ChannelParticipantLeft):
        return 'missing'
    if isinstance(member, raw.types.ChannelParticipantBanned):
        return 'blocked'
    return 'unverified'


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


def repair_client(credentials):
    api_id, api_hash, session = credentials
    return Client('worker_repair', api_id=api_id, api_hash=api_hash,
                  session_string=session, in_memory=True, no_updates=True, sleep_threshold=0)


async def _get_channel(client, channel_id):
    try:
        return await client.get_chat(channel_id)
    except PeerIdInvalid:
        # A portable string session contains no cached peers. Load the owner's dialogs.
        async for dialog in client.get_dialogs(limit=300):
            if dialog.chat.id == channel_id:
                return dialog.chat
        raise RepairError('The channel was not found in the session account’s recent chats.')


async def _add_bot(client, channel, worker):
    peer = await client.resolve_peer(worker.username)
    if not isinstance(peer, raw.types.InputPeerUser) or peer.user_id != worker.id:
        raise RepairError('Could not verify the worker identity.')
    # Invoke the edit directly. The high-level SDK promotion helper catches all
    # errors from an internal membership check, including Telegram FloodWait.
    await client.invoke(raw.functions.channels.EditAdmin(
        channel=raw.types.InputChannel(channel_id=channel.channel_id, access_hash=channel.access_hash),
        user_id=raw.types.InputUser(user_id=peer.user_id, access_hash=peer.access_hash),
        admin_rights=raw.types.ChatAdminRights(other=True),
        rank='',
    ), retries=1, sleep_threshold=0)


async def repair_channel(channel_id, report):
    state = db.db['worker_repair']
    key = {'_id': Telegram.OWNER_ID}
    checkpoint = await state.find_one(key) or {}
    try:
        credentials = repair_credentials(checkpoint)
    except InvalidTag:
        return 'The saved session could not be opened. Run /workersetup to replace it.'
    if credentials is None:
        return 'Run /workersetup in the owner’s private chat to connect the repair account.'
    next_at = checkpoint.get('next_at', 0)
    if next_at > time.time():
        return f'Repair is paused. Try again in {math.ceil(next_at - time.time())} seconds.'

    workers = {worker.me.id: worker.me for worker in worker_clients
               if worker is not TelegramBot and worker.is_connected and worker.me
               and worker.me.is_bot and worker.me.username}
    if not workers:
        return 'No connected auxiliary workers with Telegram usernames.'
    added = present = blocked = 0
    client = repair_client(credentials)

    async def pause_until(when):
        await state.update_one(key, {'$max': {'next_at': when}}, upsert=True)

    try:
        async with asyncio.timeout(30):
            if not await client.connect():
                return 'The configured repair session is signed out. Run /workersetup again.'
            owner = await client.get_me()
            if owner.is_bot or owner.id != Telegram.OWNER_ID:
                return 'The repair session must belong to the Telegram account matching OWNER_ID.'
            chat = await _get_channel(client, channel_id)
            if chat.type not in CHANNEL_TYPES:
                return 'Repair supports Telegram channels and supergroups.'
            member = await client.get_chat_member(channel_id, 'me')
            if not (member.status == ChatMemberStatus.OWNER or
                    member.status == ChatMemberStatus.ADMINISTRATOR and member.privileges
                    and member.privileges.can_promote_members):
                return 'The session account needs permission to add administrators in this channel.'
            channel = await client.resolve_peer(channel_id)
            if not isinstance(channel, raw.types.InputPeerChannel):
                return 'Could not resolve the channel safely.'

        for worker in workers.values():
            async with asyncio.timeout(20):
                status = await membership(client, channel_id, worker.username, expected_id=worker.id)
            if status == 'present':
                present += 1
                continue
            if status != 'missing':
                blocked += 1
                continue
            delay = next_at - time.time()
            if delay > 0:
                await asyncio.sleep(delay)
                async with asyncio.timeout(20):
                    status = await membership(client, channel_id, worker.username, expected_id=worker.id)
                if status != 'missing':
                    present += status == 'present'
                    blocked += status != 'present'
                    continue
            # Save the interval before any write to Telegram, even if it times out.
            next_at = time.time() + ADD_INTERVAL
            await pause_until(next_at)
            async with asyncio.timeout(20):
                await _add_bot(client, channel, worker)
            added += 1
            await report(f'Added @{worker.username} · {added} added so far\n'
                         f'Channel {channel_id} · At least {ADD_INTERVAL}s between additions')
        return f'Channel {channel_id}\nAdded {added} · Already present {present} · Skipped {blocked}'
    except FloodWait as error:
        wait = max(ADD_INTERVAL, error.value)
        await pause_until(time.time() + wait)
        return f'Telegram rate-limited the repair. Added {added}. Wait at least {wait} seconds before trying again.'
    except PeerFlood:
        return f'Telegram restricted the account. Added {added}. Review the account before trying again.'
    except RepairError as error:
        return f'Repair stopped after {added} additions. {error}'
    except Exception as error:
        return f'Repair stopped after {added} additions ({type(error).__name__}). Check channel permissions and try again.'
    finally:
        if client.is_connected:
            try:
                await client.disconnect()
            except Exception:
                pass
