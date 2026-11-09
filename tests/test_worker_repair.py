import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from hydrogram import raw
from hydrogram.enums import ChatMemberStatus, ChatType
from hydrogram.errors import ChatAdminRequired, FloodWait, UserNotParticipant

with patch.dict(os.environ, {
    'TELEGRAM_API_ID': '12345', 'TELEGRAM_API_HASH': 'test_hash',
    'TELEGRAM_BOT_TOKEN': '12345:test_token', 'TELEGRAM_BOT_USERNAME': 'TestGateStreamBot',
    'OWNER_ID': '12345', 'DATABASE_URL': 'mongodb://localhost:27017',
    'SPACE_ID': 'user/app', 'SPACE_HOST': 'user-app.hf.space',
}, clear=True):
    from bot.modules import worker_repair as repair, worker_setup


CHANNEL = -100123
PEER = raw.types.InputPeerChannel(channel_id=123, access_hash=456)
OWNER = 12345


def worker(ident):
    return SimpleNamespace(is_connected=True, me=SimpleNamespace(id=ident, username=f'worker_{ident}', is_bot=True))


class WorkerDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_membership_distinguishes_missing_from_permission_failure(self):
        profile = worker(7)
        observer = SimpleNamespace(resolve_peer=AsyncMock(side_effect=lambda value:
            PEER if isinstance(value, int) and value < 0 else raw.types.InputPeerUser(user_id=7, access_hash=5)),
            invoke=AsyncMock(side_effect=[ChatAdminRequired(), UserNotParticipant()]))
        channels = [CHANNEL, CHANNEL - 1]
        with patch.object(repair, 'worker_clients', [profile]), patch.object(repair, 'TelegramBot', observer):
            rows, note = await repair.diagnose(channels)
        self.assertEqual(rows[0][2], {CHANNEL: 'unverified', CHANNEL - 1: 'missing'})
        self.assertFalse(note)
        self.assertIn('1 missing · 1 unverified', repair.diagnostic_text(channels, rows))

    async def test_addition_uses_only_minimum_rights_and_never_auto_retries(self):
        client = SimpleNamespace(resolve_peer=AsyncMock(return_value=raw.types.InputPeerUser(user_id=7, access_hash=8)),
                                 invoke=AsyncMock())
        await repair._add_bot(client, PEER, worker(7).me)
        call = client.invoke.call_args
        request = call.args[0]
        self.assertIsInstance(request, raw.functions.channels.EditAdmin)
        self.assertEqual(request.user_id.user_id, 7)
        self.assertEqual(request.channel.channel_id, 123)
        self.assertTrue(request.admin_rights.other)
        self.assertFalse(request.admin_rights.add_admins)
        self.assertFalse(request.admin_rights.post_messages)
        self.assertEqual(call.kwargs, {'retries': 1, 'sleep_threshold': 0})


class WorkerRepairTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 1000
        self.next_at = 0
        self.scheduled = []
        self.saved = {}
        self.workers = [worker(7), worker(8), worker(9), worker(10)]
        self.client = SimpleNamespace(is_connected=True, connect=AsyncMock(return_value=True),
            get_me=AsyncMock(return_value=SimpleNamespace(id=OWNER, is_bot=False)),
            get_chat=AsyncMock(return_value=SimpleNamespace(type=ChatType.CHANNEL)),
            get_chat_member=AsyncMock(return_value=SimpleNamespace(status=ChatMemberStatus.OWNER)),
            resolve_peer=AsyncMock(return_value=PEER), disconnect=AsyncMock())
        self.create_client = Mock(return_value=self.client)
        collection = SimpleNamespace(find_one=self.find, update_one=self.update)
        for target, key, value in (
            (repair.db, 'db', {'worker_repair': collection}), (repair, 'time', SimpleNamespace(time=lambda: self.now)),
            (repair, 'worker_clients', self.workers), (repair, 'repair_client', self.create_client),
            (repair.Telegram, 'REPAIR_API_ID', 1), (repair.Telegram, 'REPAIR_API_HASH', 'hash'),
            (repair.Telegram, 'REPAIR_SESSION', 'test'),
        ):
            context = patch.object(target, key, value)
            context.start()
            self.addCleanup(context.stop)

    async def find(self, query):
        return {'next_at': self.next_at, **self.saved}

    async def update(self, query, update, *, upsert):
        if '$set' in update:
            self.saved.update(update['$set'])
        else:
            self.next_at = max(self.next_at, update['$max']['next_at'])
            self.scheduled.append(self.next_at)

    async def test_repair_adds_only_missing_workers_with_persisted_spacing(self):
        added = []
        await worker_setup._save_credentials(2, 'saved-hash', 'saved-session')

        async def sleep(seconds):
            self.now += seconds

        async def inspect(client, channel, username, *, expected_id):
            return {7: 'missing', 8: 'present', 9: 'blocked', 10: 'missing'}[expected_id]

        async def add(client, channel, profile):
            self.assertGreaterEqual(self.next_at, self.now + 60)
            added.append((profile.id, self.now))

        with patch.object(repair, 'membership', side_effect=inspect), \
             patch.object(repair.asyncio, 'sleep', side_effect=sleep), \
             patch.object(repair, '_add_bot', side_effect=add):
            summary = await repair.repair_channel(CHANNEL, AsyncMock())
        self.create_client.assert_called_once_with([2, 'saved-hash', 'saved-session'])
        self.assertEqual(added, [(7, 1000), (10, 1060)])
        self.assertEqual(self.scheduled, [1060, 1120])
        self.assertIn('Added 2 · Already present 1 · Skipped 1', summary)
        self.client.disconnect.assert_awaited_once()

    async def test_flood_wait_stops_and_blocks_a_new_run_until_telegram_deadline(self):
        with patch.object(repair, 'membership', AsyncMock(return_value='missing')), \
             patch.object(repair, '_add_bot', AsyncMock(side_effect=FloodWait(240))) as add:
            result = await repair.repair_channel(CHANNEL, AsyncMock())
            self.assertIn('240 seconds', result)
            self.assertEqual(self.next_at, 1240)
            result = await repair.repair_channel(CHANNEL, AsyncMock())
            self.assertIn('paused', result)
        add.assert_awaited_once()
        self.client.connect.assert_awaited_once()

    async def test_repair_requires_owner_session_and_permission(self):
        self.client.get_me.return_value = SimpleNamespace(id=OWNER + 1, is_bot=False)
        with patch.object(repair, '_add_bot', AsyncMock()) as add:
            result = await repair.repair_channel(CHANNEL, AsyncMock())
            self.assertIn('OWNER_ID', result)
            self.client.get_me.return_value = SimpleNamespace(id=OWNER, is_bot=False)
            self.client.get_chat_member.return_value = SimpleNamespace(
                status=ChatMemberStatus.ADMINISTRATOR, privileges=SimpleNamespace(can_promote_members=False))
            result = await repair.repair_channel(CHANNEL, AsyncMock())
            self.assertIn('permission', result)
        add.assert_not_awaited()
        self.assertFalse(self.scheduled)
