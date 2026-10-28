import asyncio
from hydrogram import idle
from bot.clients import (
    start_all_clients,
    stop_all_clients,
    TelegramBot,
    worker_clients,
    record_heartbeat_ping
)
from bot.server import server
from bot.modules.memory import flush_ram, check_memory_circuit_breaker

from logging import getLogger
logger = getLogger('heartbeat')

async def keep_alive_heartbeat():
    """Keeps all Telegram MTProto worker sockets warm, auto-reconnects dead sockets, and compacts RAM every 2 minutes."""
    # Short initial delay on boot
    await asyncio.sleep(5)
    while True:
        try:
            active_pings = 0
            for client in list(worker_clients):
                if client:
                    try:
                        if not getattr(client, 'is_connected', False):
                            await client.connect()
                        await client.get_me()
                        active_pings += 1
                    except Exception:
                        try:
                            # Re-establish dead connection socket
                            await client.connect()
                            await client.get_me()
                            active_pings += 1
                        except Exception:
                            pass
            record_heartbeat_ping()
            logger.info("💓 Telegram DC heartbeat sent across %d worker(s).", active_pings)
            # Auto-compact RAM & check strict memory ceiling
            check_memory_circuit_breaker()
            flush_ram()
            await asyncio.sleep(120)  # Every 2 minutes
        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(10)

async def main():
    await start_all_clients()
    server_task = TelegramBot.loop.create_task(server.serve())
    heartbeat_task = TelegramBot.loop.create_task(keep_alive_heartbeat())
    await idle()
    heartbeat_task.cancel()
    await stop_all_clients()

if __name__ == '__main__':
    TelegramBot.loop.run_until_complete(main())
