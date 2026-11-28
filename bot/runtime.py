"""One event loop owns signals, services, background work, and their shutdown."""
import asyncio
import logging
import os
import platform
import signal
import socket
import sys
from concurrent.futures import ThreadPoolExecutor

from bot import version
from bot.config import Resources, Server, Telegram, validate_config
from bot.modules import lifecycle

logger = logging.getLogger('bot')


def bind_listener():
    """Reserve the actual listener before starting Telegram or database services."""
    try:
        addresses = socket.getaddrinfo(Server.BIND_ADDRESS, Server.PORT, type=socket.SOCK_STREAM,
                                       flags=socket.AI_PASSIVE)
    except OSError:
        raise RuntimeError('HTTP bind address could not be resolved. Check BIND_ADDRESS.') from None
    for family, kind, proto, _, address in addresses:
        listener = socket.socket(family, kind, proto)
        try:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if family == socket.AF_INET6:
                listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            listener.bind(address)
            listener.listen(128)
            listener.setblocking(False)
            return listener
        except OSError:
            listener.close()
    raise RuntimeError('HTTP bind failed. Check BIND_ADDRESS, PORT, port reservation, and whether another instance is running.')


async def keep_alive_heartbeat():
    from bot.clients import worker_clients, record_heartbeat_ping

    await asyncio.sleep(5)
    while True:
        for client in tuple(worker_clients):
            # Failed authentication is retried only after configuration is fixed and restarted.
            if not client.me or getattr(client, '_gatestream_closed', False):
                continue
            try:
                async with asyncio.timeout(10):
                    if not client.is_connected:
                        await client.connect()
                    await client.get_me()
            except Exception as error:
                logger.warning('Worker heartbeat failed (%s).', type(error).__name__)
        record_heartbeat_ping()
        await asyncio.sleep(120)


async def main():
    validate_config()
    from bot import clients
    from bot.database import db
    from bot.server import server
    from bot.modules.activity_store import activity_store
    from bot.modules.memory import monitor_memory

    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=Resources.IO_WORKERS, thread_name_prefix='gatestream-io'))
    stop = asyncio.Event()
    lifecycle.begin(stop)
    previous_signals = {}
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            previous_signals[sig] = signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
        except (ValueError, OSError):
            pass
    logger.info('GateStream %s | %s %s | Python %s', version, platform.system(), platform.release(), platform.python_version())
    logger.info('HTTP configured: %s:%d | Media cache: %d MiB | Configured bot clients: %d',
                Server.BIND_ADDRESS, Server.PORT, Resources.MEDIA_CACHE_MAX_MB, len(Telegram.WORKER_TOKENS))
    if Telegram.IGNORED_WORKER_TOKENS:
        logger.warning('Ignored %d blank, placeholder, or malformed worker entries.', Telegram.IGNORED_WORKER_TOKENS)
    listener = None
    startup_task = http_task = None
    stop_task = asyncio.create_task(stop.wait())
    try:
        listener = bind_listener()
        startup_task = asyncio.create_task(clients.start_all_clients())
        done, _ = await asyncio.wait((startup_task, stop_task), return_when=asyncio.FIRST_COMPLETED)
        if startup_task not in done:
            return False
        await startup_task
        http_task = asyncio.create_task(server.serve(sockets=[listener]))
        while not server.started and not http_task.done() and not stop.is_set():
            await asyncio.sleep(.01)
        if http_task.done():
            await http_task
            raise RuntimeError('HTTP startup failed. Check the server and database configuration.')
        if stop.is_set():
            return False
        logger.info('HTTP server ready at %s:%d.', Server.BIND_ADDRESS, Server.PORT)
        lifecycle.spawn(monitor_memory(stop))
        lifecycle.spawn(keep_alive_heartbeat())
        lifecycle.spawn(clients.notify_restart())
        done, _ = await asyncio.wait((http_task, stop_task), return_when=asyncio.FIRST_COMPLETED)
        if http_task in done and not stop.is_set():
            await http_task
            raise RuntimeError('HTTP server stopped unexpectedly.')
        return lifecycle.restart_requested
    finally:
        if startup_task and not startup_task.done():
            startup_task.cancel()
            await asyncio.gather(startup_task, return_exceptions=True)
        if http_task:
            server.should_exit = True
            try:
                await asyncio.wait_for(asyncio.shield(http_task), Server.SHUTDOWN_TIMEOUT + 5)
            except (Exception, asyncio.CancelledError):
                http_task.cancel()
                await asyncio.gather(http_task, return_exceptions=True)
        stop_task.cancel()
        await asyncio.gather(stop_task, return_exceptions=True)
        # Each resource gets a cleanup attempt even if another resource fails.
        for cleanup in (lifecycle.close, activity_store.flush, clients.stop_all_clients):
            try:
                async with asyncio.timeout(Server.SHUTDOWN_TIMEOUT):
                    await cleanup()
            except Exception as error:
                logger.warning('Service cleanup failed (%s).', type(error).__name__)
        try:
            db.close()
        finally:
            if listener is not None:
                listener.close()
            for sig, previous in previous_signals.items():
                signal.signal(sig, previous)
            logger.info('GateStream shutdown complete.')


def run():
    try:
        restart = asyncio.run(main())
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        logger.error('Application stopped (%s). %s', type(error).__name__, error if isinstance(error, RuntimeError) else 'Check configuration and service availability.')
        return 1
    if restart:
        os.execl(sys.executable, sys.executable, '-m', 'bot')
    return 0
