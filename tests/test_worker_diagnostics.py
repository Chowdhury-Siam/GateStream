import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from hydrogram import raw
from hydrogram.errors import ChatWriteForbidden, FloodWait

with patch.dict(os.environ, {
    'TELEGRAM_API_ID': '12345', 'TELEGRAM_API_HASH': 'test_hash',
    'TELEGRAM_BOT_TOKEN': '12345:test_token', 'TELEGRAM_BOT_USERNAME': 'TestGateStreamBot',
    'OWNER_ID': '12345', 'DATABASE_URL': 'mongodb://localhost:27017',
    'SPACE_ID': 'user/app', 'SPACE_HOST': 'user-app.hf.space',
}, clear=True):
    from bot.modules import worker_diagnostics as diagnostics
    with patch.object(diagnostics.TelegramBot, 'add_handler'):
        from bot.plugins import commands


CHANNEL = -100123
PEER = raw.types.InputPeerChannel(channel_id=123, access_hash=456)
OWNER = 12345


def worker(ident):
    return SimpleNamespace(is_connected=True, me=SimpleNamespace(id=ident, username=f'worker_{ident}', is_bot=True))


class WorkerDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_workers_command_probes_each_bot_and_lists_only_failures(self):
        pool = [worker(index) for index in range(5)]
        for index, client in enumerate(pool):
            client.resolve_peer = AsyncMock(return_value=PEER)
            client.rnd_id = Mock(return_value=index)
            client.invoke = AsyncMock()
        pool[2].invoke.side_effect = ChatWriteForbidden()
        pool[3].invoke.side_effect = FloodWait(24)
        pool[4].is_connected = False
        status = SimpleNamespace(edit_text=AsyncMock())
        msg = SimpleNamespace(from_user=SimpleNamespace(id=OWNER), command=['workers'],
                              reply=AsyncMock(return_value=status))
        with patch.object(commands.Telegram, 'CHANNEL_ID', CHANNEL), \
             patch.object(diagnostics, 'TelegramBot', pool[0]), patch.object(diagnostics, 'worker_clients', pool):
            await commands.workers_command(None, msg)

        for index, client in enumerate(pool[:4]):
            client.resolve_peer.assert_awaited_once_with(CHANNEL)
            client.invoke.assert_awaited_once()
            call = client.invoke.await_args
            request, = call.args
            self.assertIsInstance(request, raw.functions.messages.SendMessage)
            self.assertIs(request.peer, PEER)
            self.assertEqual(request.random_id, index)
            self.assertTrue(request.silent)
            self.assertIn(f'@worker_{index}', request.message)
            self.assertEqual(call.kwargs, {'retries': 1, 'sleep_threshold': 0})
        pool[4].invoke.assert_not_awaited()
        text, = status.edit_text.await_args.args
        self.assertNotIn('@worker_0', text)
        self.assertNotIn('@worker_1', text)
        self.assertIn('3 of 5 bots could not post', text)
        self.assertIn('@worker_2 · Posting permission denied', text)
        self.assertIn('@worker_3 · Rate limited for 24s', text)
        self.assertIn('@worker_4 · Offline', text)
