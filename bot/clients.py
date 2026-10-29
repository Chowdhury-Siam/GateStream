from hydrogram import Client, enums
from logging import getLogger
from itertools import cycle
from bot.config import Telegram
from bot.database import db

logger = getLogger('bot')

# Main Bot Client (Processes user commands, interactions, and updates)
TelegramBot = Client(
    name='bot_main',
    api_id=Telegram.API_ID,
    api_hash=Telegram.API_HASH,
    bot_token=Telegram.BOT_TOKEN,
    parse_mode=enums.ParseMode.MARKDOWN,
    plugins=dict(root='bot.plugins'),
    sleep_threshold=-1,
    max_concurrent_transmissions=10,
    ipv6=False
)

# Multi-Client Worker Pool (Used for fast parallel chunk downloading and streaming)
worker_clients: list[Client] = []
_worker_cycler = None

LAST_HEARTBEAT_TIME = 0.0
HEARTBEAT_PINGS_COUNT = 0

def record_heartbeat_ping():
    global LAST_HEARTBEAT_TIME, HEARTBEAT_PINGS_COUNT
    import time
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
    for idx, token in enumerate(Telegram.MULTI_BOT_TOKENS, start=1):
        if token and token != Telegram.BOT_TOKEN:
            worker = Client(
                name=f'worker_{idx}',
                api_id=Telegram.API_ID,
                api_hash=Telegram.API_HASH,
                bot_token=token,
                sleep_threshold=-1,
                max_concurrent_transmissions=10,
                workers=1,
                in_memory=True,
                no_updates=True,
                ipv6=False
            )
            worker_clients.append(worker)
        
    _worker_cycler = cycle(worker_clients)
    logger.info("Initialized %d bot client(s) in worker pool.", len(worker_clients))

_worker_cooldowns: dict[str, float] = {}

def mark_worker_cooldown(worker_name: str, duration: float):
    """Temporarily marks a worker bot on cooldown if rate-limited by Telegram."""
    import time
    _worker_cooldowns[worker_name] = time.time() + duration
    logger.warning("Worker %s placed on cooldown for %.1fs due to Telegram rate-limiting.", worker_name, duration)

def get_worker_client() -> Client:
    """Returns the next healthy worker client from the pool, skipping any currently on cooldown."""
    global _worker_cycler
    if not worker_clients:
        return TelegramBot

    import time
    now = time.time()
    # Filter healthy clients (cooldown expired or not set)
    healthy_workers = [
        w for w in worker_clients
        if now >= _worker_cooldowns.get(getattr(w, 'name', ''), 0)
    ]

    # If all auxiliary workers are on cooldown, fall back to TelegramBot
    if not healthy_workers:
        return TelegramBot

    # Cycle among currently healthy workers
    if _worker_cycler is None:
        _worker_cycler = cycle(worker_clients)

    for _ in range(len(worker_clients)):
        candidate = next(_worker_cycler)
        if candidate in healthy_workers:
            return candidate

    return healthy_workers[0]

async def start_all_clients():
    import os
    import time

    # Connect MongoDB
    db.connect()
    await db.db.command('ping')
    from bot.modules.activity_store import activity_store
    await activity_store.flush()
    
    # Start Main Bot
    logger.info("Starting Main Telegram Bot...")
    await TelegramBot.start()
    if TelegramBot.me and TelegramBot.me.username:
        Telegram.BOT_USERNAME = TelegramBot.me.username
        logger.info("Auto-detected Telegram Bot username: @%s", Telegram.BOT_USERNAME)
    await db.create_indexes()

    # Check and resolve pending restart notification
    if os.path.exists('.restart_state.txt'):
        try:
            with open('.restart_state.txt', 'r') as f:
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
                        f"🚀 All worker bots, MongoDB connection, and web server are active."
                    )
                )
        except Exception as e:
            logger.warning("Failed to edit restart notification: %s", e)
        finally:
            try:
                os.remove('.restart_state.txt')
            except Exception:
                pass
    
    # Start Worker Clients
    init_worker_clients()
    logger.info("Starting Worker Clients pool (%d total)...", len(worker_clients))
    for idx, client in enumerate(worker_clients):
        if client == TelegramBot:
            continue
        try:
            await client.start()
            bot_username = getattr(client.me, 'username', 'N/A')
            logger.info("Worker client #%d (@%s) started successfully.", idx, bot_username)
        except Exception as e:
            logger.warning("Failed to start worker client #%d: %s", idx, e)

def get_all_worker_clients() -> list[Client]:
    """Returns a list of all active worker clients (including TelegramBot as worker 0)."""
    return list(worker_clients)

async def stop_all_clients():
    logger.info("Stopping all clients...")
    for client in worker_clients:
        if client == TelegramBot:
            continue
        try:
            if client.is_connected:
                await client.stop()
        except Exception:
            pass
    if TelegramBot.is_connected:
        await TelegramBot.stop()
    logger.info("All clients stopped.")
