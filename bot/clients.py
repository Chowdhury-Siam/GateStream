import asyncio
import time
from hydrogram import Client, enums
from hydrogram.errors import AccessTokenExpired, AccessTokenInvalid
from logging import getLogger
from itertools import cycle
from bot.config import PROJECT_ROOT, Resources, Telegram
from bot.database import db
from bot.modules.forum_updates import install_forum_parser

logger = getLogger('bot')

# Main Bot Client (Processes user commands, interactions, and updates)
TelegramBot = Client(
    name='bot_main',
    workdir=str(PROJECT_ROOT),
    workers=Resources.HANDLER_WORKERS,
    api_id=Telegram.API_ID,
    api_hash=Telegram.API_HASH,
    bot_token=Telegram.BOT_TOKEN,
    parse_mode=enums.ParseMode.MARKDOWN,
    # Explicit imports keep retired plugins from loading after an overlay upgrade.
    plugins=dict(root='bot.plugins', include=[
        'access', 'admin', 'callback', 'commands', 'files', 'link', 'library',
    ]),
    sleep_threshold=-1,
    max_concurrent_transmissions=Resources.MAX_TRANSMISSIONS,
    ipv6=False
)
# Hydrogram otherwise retains up to 10,000 complete message objects per client.
TelegramBot.message_cache.capacity = 512
install_forum_parser(TelegramBot)

# Multi-Client Worker Pool (Used for fast parallel chunk downloading and streaming)
worker_clients: list[Client] = []
_worker_cycler = None

LAST_HEARTBEAT_TIME = 0.0
HEARTBEAT_PINGS_COUNT = 0

def record_heartbeat_ping():
    global LAST_HEARTBEAT_TIME, HEARTBEAT_PINGS_COUNT
    LAST_HEARTBEAT_TIME = time.time()
    HEARTBEAT_PINGS_COUNT += 1

def get_heartbeat_status() -> tuple[int, float]:
    return HEARTBEAT_PINGS_COUNT, LAST_HEARTBEAT_TIME

def init_worker_clients():
    global worker_clients, _worker_cycler
    worker_clients.clear()
    
    # 1. The primary TelegramBot is worker #0 (reuse existing connection, no duplicate session!)
    worker_clients.append(TelegramBot)
    
    # 2. Add extra worker bots from MULTI_BOT_TOKENS
    for idx, token in enumerate(dict.fromkeys(Telegram.MULTI_BOT_TOKENS), start=1):
        if token and token != Telegram.BOT_TOKEN:
            worker = Client(
                name=f'worker_{idx}',
                api_id=Telegram.API_ID,
                api_hash=Telegram.API_HASH,
                bot_token=token,
                sleep_threshold=-1,
                max_concurrent_transmissions=Resources.MAX_TRANSMISSIONS,
                workers=1,
                in_memory=True,
                no_updates=True,
                ipv6=False
            )
            worker.message_cache.capacity = 128
            worker_clients.append(worker)
        
    _worker_cycler = cycle(worker_clients)
    logger.info("Initialized %d bot client(s) in worker pool.", len(worker_clients))

_worker_cooldowns: dict[str, float] = {}

def mark_worker_cooldown(worker_name: str, duration: float):
    """Temporarily marks a worker bot on cooldown if rate-limited by Telegram."""
    _worker_cooldowns[worker_name] = time.monotonic() + duration
    logger.warning("Worker %s cooling down for %.1fs after a media read failure or Telegram limit.", worker_name, duration)

def get_worker_client() -> Client:
    """Returns the next healthy worker client from the pool, skipping any currently on cooldown."""
    global _worker_cycler
    if len(worker_clients) <= 1:
        return TelegramBot

    if _worker_cycler is None:
        _worker_cycler = cycle(worker_clients)
    now = time.monotonic()
    for _ in worker_clients:
        candidate = next(_worker_cycler)
        if (candidate.is_connected and candidate.me and not getattr(candidate, '_gatestream_closed', False)
                and now >= _worker_cooldowns.get(candidate.name, 0)):
            return candidate
    return TelegramBot


async def start_all_clients():
    # Connect MongoDB
    try:
        db.connect()
        async with asyncio.timeout(15):
            await db.db.command('ping')
    except Exception as error:
        logger.error('MongoDB connection failed (%s).', type(error).__name__)
        raise
    logger.info('MongoDB connection verified.')
    
    # Start Main Bot
    logger.info("Starting Main Telegram Bot...")
    await TelegramBot.start()
    if TelegramBot.me and TelegramBot.me.username:
        Telegram.BOT_USERNAME = TelegramBot.me.username
        logger.info("Auto-detected Telegram Bot username: @%s", Telegram.BOT_USERNAME)
    await db.create_indexes()
    from bot.modules.bot_menu import register_commands
    await register_commands(TelegramBot)

    # A failed worker stays configured for /workers, but is not retried by the heartbeat.
    # Start Worker Clients
    init_worker_clients()
    logger.info("Starting Worker Clients pool (%d total)...", len(worker_clients))
    for idx, client in enumerate(worker_clients):
        if client == TelegramBot:
            continue
        try:
            async with asyncio.timeout(30):
                await client.start()
            bot_username = getattr(client.me, 'username', 'N/A')
            logger.info("Worker client #%d (@%s) started successfully.", idx, bot_username)
        except Exception as error:
            logger.warning('Worker #%d could not start (%s).', idx, type(error).__name__)
            await close_client(client)
            if isinstance(error, (AccessTokenInvalid, AccessTokenExpired)):
                logger.warning('Worker #%d authentication failed. Check its configured token.', idx)
    connected = sum(bool(client.is_connected and client.me) for client in worker_clients)
    logger.info('Worker clients connected: %d/%d (including primary).', connected, len(worker_clients))

async def close_client(client, *, idempotent=True):
    if idempotent and getattr(client, '_gatestream_closed', False):
        return
    client._gatestream_closed = True
    try:
        async with asyncio.timeout(10):
            if getattr(client, 'is_initialized', False):
                await client.stop()
            elif client.is_connected:
                await client.disconnect()
            else:
                # connect() may have opened storage/transport before it was canceled.
                if getattr(client, 'session', None) is not None:
                    await client.session.stop()
                if getattr(client, 'storage', None) is not None:
                    await client.storage.close()
    except Exception as error:
        logger.warning('Telegram client cleanup failed (%s); closing remaining transports.', type(error).__name__)
        # A long command can exhaust stop()'s dispatcher deadline. Still close the
        # transports and storage before asyncio.run shuts down its executor.
        sessions = list(getattr(client, 'media_sessions', {}).values())
        if getattr(client, 'session', None) is not None:
            sessions.append(client.session)
        for session in sessions:
            try:
                async with asyncio.timeout(3):
                    await session.stop()
            except Exception:
                logger.warning('Telegram transport cleanup could not complete.')
        if getattr(client, 'storage', None) is not None:
            try:
                await client.storage.close()
            except Exception:
                logger.warning('Telegram storage cleanup could not complete.')
    finally:
        executor = getattr(client, 'executor', None)
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)


async def stop_all_clients():
    logger.info('Stopping Telegram clients...')
    outcomes = await asyncio.gather(*(close_client(client) for client in
        dict.fromkeys([*worker_clients, TelegramBot])), return_exceptions=True)
    for outcome in outcomes:
        if isinstance(outcome, Exception):
            logger.warning('Telegram cleanup failed (%s).', type(outcome).__name__)
    logger.info('Telegram clients stopped.')


async def notify_restart():
    import os
    if (PROJECT_ROOT / '.restart_state.txt').exists():
        try:
            with open(PROJECT_ROOT / '.restart_state.txt', 'r') as f:
                parts = f.read().strip().split()
            if len(parts) >= 2:
                chat_id = int(parts[0])
                msg_id = int(parts[1])
                duration_str = ""
                if len(parts) >= 3:
                    elapsed = round(time.time() - float(parts[2]), 1)
                    duration_str = f" in `{elapsed}s`"
                await TelegramBot.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text=(
                        f"✅ **GateStream Bot rebooted successfully{duration_str}!**\n\n"
                        f"🚀 MongoDB is connected and the HTTP server is ready. See /workers for fleet status."
                    )
                )
        except Exception as error:
            logger.warning('Could not send restart notice (%s).', type(error).__name__)
        finally:
            try:
                os.remove(PROJECT_ROOT / '.restart_state.txt')
            except Exception:
                pass
