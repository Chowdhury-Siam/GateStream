"""Exercise startup failures, fleet isolation, handlers and real HTTP signal shutdown."""
import asyncio
from contextlib import ExitStack
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

with patch.dict(os.environ, {
    'TELEGRAM_API_ID':'12345', 'TELEGRAM_API_HASH':'test',
    'TELEGRAM_BOT_TOKEN':'12345:test', 'OWNER_ID':'12345',
    'DATABASE_URL':'mongodb://localhost:27017',
}, clear=True):
    from bot import clients, runtime
    from bot.config import Resources, Server, Telegram
    from bot.modules import lifecycle
    import bot.server as web


class WorkerStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_worker_cannot_block_primary_or_valid_workers(self):
        from hydrogram.errors import AccessTokenInvalid
        from bot.modules.activity_store import activity_store
        from bot.modules import bot_menu
        def worker(name):
            obj=Mock(name=name)
            obj.name=name
            obj._gatestream_closed=False
            obj.is_connected=False
            obj.is_initialized=False
            obj.session=None
            obj.storage.close=AsyncMock()
            obj.me=None
            async def start():
                obj.me=SimpleNamespace(username=name)
                obj.is_connected=True
            obj.start=AsyncMock(side_effect=start)
            return obj
        primary,bad,good=worker('primary'),worker('bad'),worker('good')
        bad.start=AsyncMock(side_effect=AccessTokenInvalid())
        with patch.multiple(clients,TelegramBot=primary,Client=Mock(side_effect=[bad,good])), \
             patch.object(Telegram,'MULTI_BOT_TOKENS',['bad','good']),patch.object(Telegram,'BOT_USERNAME',Telegram.BOT_USERNAME), \
             patch.object(clients.db,'connect'),patch.object(clients.db,'db',SimpleNamespace(command=AsyncMock())), \
             patch.object(clients.db,'create_indexes',AsyncMock()),patch.object(activity_store,'flush',AsyncMock()), \
             patch.object(bot_menu,'register_commands',AsyncMock()):
            saved=clients.worker_clients[:]
            try:
                await clients.start_all_clients()
                primary.start.assert_awaited_once()
                good.start.assert_awaited_once()
                bad.executor.shutdown.assert_called_once()
                # Even stale SDK connection flags cannot resurrect a closed worker.
                bad.is_connected=True
                bad.me=SimpleNamespace(username='bad')
                for _ in range(6):
                    self.assertIn(clients.get_worker_client(),[primary,good])
                self.assertTrue(bad._gatestream_closed)
            finally:
                clients.worker_clients[:]=saved
                clients._worker_cycler=None

    def test_disabled_workers_reuse_only_primary(self):
        saved=clients.worker_clients[:]
        try:
            with patch.object(Telegram,'MULTI_BOT_TOKENS',[]),patch.object(clients,'Client') as constructor:
                clients.init_worker_clients()
                self.assertEqual(clients.worker_clients,[clients.TelegramBot])
                constructor.assert_not_called()
        finally:
            clients.worker_clients[:]=saved
            clients._worker_cycler=None

    async def test_client_cleanup_stops_transport_and_executor_once(self):
        client=SimpleNamespace(is_initialized=True,is_connected=True,stop=AsyncMock(),executor=Mock())
        await clients.close_client(client)
        await clients.close_client(client)
        client.stop.assert_awaited_once()
        client.executor.shutdown.assert_called_once_with(wait=True,cancel_futures=True)


class ApplicationLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        await lifecycle.close()
        lifecycle.begin(asyncio.Event())

    async def test_bind_failure_never_starts_telegram_and_closes_services(self):
        from bot.database import db
        from bot.modules.worker_setup import setup
        from bot.modules.archive_cache import archive_cache
        from bot.modules.activity_store import activity_store
        with ExitStack() as stack:
            stack.enter_context(patch.object(runtime,'bind_listener',side_effect=RuntimeError('HTTP bind failed')))
            start=stack.enter_context(patch.object(clients,'start_all_clients',AsyncMock()))
            stop=stack.enter_context(patch.object(clients,'stop_all_clients',AsyncMock()))
            closed=stack.enter_context(patch.object(db,'close'))
            for resource,name in ((setup,'close'),(archive_cache,'close'),(activity_store,'flush')):
                stack.enter_context(patch.object(resource,name,AsyncMock()))
            with self.assertRaisesRegex(RuntimeError,'HTTP bind failed'):
                await runtime.main()
            start.assert_not_awaited()
            stop.assert_awaited_once()
            closed.assert_called_once()

    async def test_failed_http_startup_never_reports_readiness_and_stops_clients(self):
        from bot.database import db
        from bot.modules.worker_setup import setup
        from bot.modules.archive_cache import archive_cache
        from bot.modules.activity_store import activity_store
        failed=SimpleNamespace(started=False,should_exit=False,serve=AsyncMock())
        listener=Mock()
        with ExitStack() as stack:
            stack.enter_context(patch.object(runtime,'bind_listener',return_value=listener))
            stack.enter_context(patch.object(web,'server',failed))
            start=stack.enter_context(patch.object(clients,'start_all_clients',AsyncMock()))
            stop=stack.enter_context(patch.object(clients,'stop_all_clients',AsyncMock()))
            closed=stack.enter_context(patch.object(db,'close'))
            for resource,name in ((setup,'close'),(archive_cache,'close'),(activity_store,'flush')):
                stack.enter_context(patch.object(resource,name,AsyncMock()))
            with self.assertLogs('bot',level='INFO') as logs:
                with self.assertRaisesRegex(RuntimeError,'HTTP startup failed'):
                    await runtime.main()
            self.assertNotIn('HTTP server ready',' '.join(logs.output))
            start.assert_awaited_once()
            stop.assert_awaited_once()
            closed.assert_called_once()
            listener.close.assert_called_once()

    def test_listener_uses_requested_port_and_reports_conflict(self):
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1',0))
            occupied.listen()
            with patch.object(Server,'BIND_ADDRESS','127.0.0.1'),patch.object(Server,'PORT',occupied.getsockname()[1]):
                with self.assertRaisesRegex(RuntimeError,'HTTP bind failed'):
                    runtime.bind_listener()
        with socket.socket() as probe:
            probe.bind(('127.0.0.1',0))
            port=probe.getsockname()[1]
        with patch.object(Server,'BIND_ADDRESS','127.0.0.1'),patch.object(Server,'PORT',port):
            listener=runtime.bind_listener()
            self.addCleanup(listener.close)
            self.assertEqual(listener.getsockname(),('127.0.0.1',port))

    async def test_background_work_drains_before_executor_shutdown_and_rejects_new_tasks(self):
        lifecycle.begin(asyncio.Event())
        entered=asyncio.Event()
        events=[]
        async def work():
            try:
                entered.set()
                await asyncio.Event().wait()
            finally:
                await asyncio.to_thread(events.append,'cleaned')
        task=lifecycle.spawn(work())
        await entered.wait()
        await lifecycle.close()
        self.assertTrue(task.cancelled())
        self.assertEqual(events,['cleaned'])
        self.assertIsNone(lifecycle.spawn(asyncio.sleep(0)))
        self.assertFalse(lifecycle.request_restart())

    async def test_memory_restart_is_cooperative(self):
        from bot.modules import memory
        stop=asyncio.Event()
        lifecycle.begin(stop)
        with patch.object(memory,'get_current_ram_mb',return_value=500),patch.object(memory,'flush_ram'), \
             patch.object(os,'execl') as execute:
            self.assertTrue(memory.check_memory_circuit_breaker(420,498))
            execute.assert_not_called()
        self.assertTrue(stop.is_set())
        self.assertTrue(lifecycle.restart_requested)


class PluginRegistrationTests(unittest.TestCase):
    def test_every_active_decorated_handler_is_discovered_once(self):
        import importlib
        import inspect
        expected=[]
        for module in clients.TelegramBot.plugins['include']:
            loaded=importlib.import_module('bot.plugins.'+module)
            for value in vars(loaded).values():
                handlers=getattr(value,'handlers',[])
                if inspect.isfunction(value) and isinstance(handlers,list):
                    expected.extend(handlers)
        self.assertGreater(len(expected),20)
        with patch.object(clients.TelegramBot,'add_handler') as add:
            with self.assertLogs('hydrogram.client',level='INFO') as logs:
                clients.TelegramBot.load_plugins()
        self.assertEqual(add.call_count,len(expected))
        self.assertEqual(len({id(call.args[0]) for call in add.call_args_list}),len(expected))
        self.assertNotIn('No plugin loaded',' '.join(logs.output))
        names={call.args[0].original_callback.__name__ for call in add.call_args_list}
        self.assertTrue({'start_command','handle_user_file','index_command'} <= names,names)


@unittest.skipUnless(os.name=='posix','POSIX signal integration')
class RealHTTPShutdownTests(unittest.TestCase):
    def test_sigint_and_sigterm_close_real_http_server_cleanly(self):
        fixture='''
from unittest.mock import AsyncMock, Mock
import bot.clients as clients
clients.start_all_clients=AsyncMock()
clients.stop_all_clients=AsyncMock()
clients.notify_restart=AsyncMock()
from bot.database import db
original_close=db.close
def close():
    original_close()
    print('DATABASE_CLOSED',flush=True)
db.close=close
from bot.modules.activity_store import activity_store
activity_store.flush=AsyncMock()
from bot.runtime import run
result=run()
print('CLIENTS_STOPPED='+str(clients.stop_all_clients.await_count),flush=True)
raise SystemExit(result)
'''
        for signum in (signal.SIGINT,signal.SIGTERM):
            with self.subTest(signum=signum),tempfile.TemporaryDirectory() as directory:
                with socket.socket() as probe:
                    probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
                env={**os.environ,'TELEGRAM_API_ID':'12345','TELEGRAM_API_HASH':'test_hash',
                     'TELEGRAM_BOT_TOKEN':'12345:test_token','OWNER_ID':'12345',
                     'DATABASE_URL':'mongodb://localhost:27017','BIND_ADDRESS':'127.0.0.1',
                     'PORT':str(port),'LOG_FILE':str(Path(directory)/'event.log')}
                process=subprocess.Popen([sys.executable,'-c',fixture],env=env,
                    cwd=Path(__file__).resolve().parents[1],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                try:
                    ready=False
                    deadline=time.monotonic()+15
                    while time.monotonic()<deadline and process.poll() is None:
                        try:
                            with socket.create_connection(('127.0.0.1',port),timeout=.2) as sock:
                                sock.sendall(b'GET /health HTTP/1.0\r\nHost: localhost\r\n\r\n')
                                ready=b'200 OK' in sock.recv(4096)
                            if ready:break
                        except OSError:
                            pass
                        time.sleep(.05)
                    self.assertTrue(ready,'HTTP never became ready')
                    # The socket can answer during the runtime's readiness polling
                    # interval; wait for its readiness report before stopping it.
                    while time.monotonic()<deadline:
                        log=Path(directory)/'event.log'
                        if log.exists() and 'HTTP server ready' in log.read_text():
                            break
                        time.sleep(.01)
                    process.send_signal(signum)
                    out,err=process.communicate(timeout=15)
                    self.assertEqual(process.returncode,0,err)
                    self.assertIn('DATABASE_CLOSED',out)
                    self.assertIn('CLIENTS_STOPPED=1',out)
                    self.assertIn('HTTP server ready at 127.0.0.1:'+str(port),err)
                    self.assertNotIn('Traceback',err)
                    self.assertNotIn('cannot schedule new futures',err)
                    with self.assertRaises(OSError):
                        socket.create_connection(('127.0.0.1',port),timeout=.2)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.communicate()
