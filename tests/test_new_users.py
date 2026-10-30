import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

with patch.dict(os.environ, {
    'TELEGRAM_API_ID': '12345', 'TELEGRAM_API_HASH': 'test_hash',
    'TELEGRAM_BOT_TOKEN': '12345:test_token', 'OWNER_ID': '12345',
    'DATABASE_URL': 'mongodb://localhost:27017',
}):
    from bot.modules import new_users
    from bot import clients


class UsersCollection:
    def __init__(self):
        self.rows = {}

    async def update_one(self, query, update, upsert=False):
        row = self.rows.get(query['_id'])
        if row is None:
            if not upsert:
                return
            row = self.rows[query['_id']] = {'_id': query['_id'], **update.get('$setOnInsert', {})}
        if any(row.get(k) != v for k, v in query.items()):
            return
        row.update(update.get('$set', {}))
        for key in update.get('$unset', {}):
            row.pop(key, None)

    async def find_one_and_update(self, query, update):
        row = self.rows[query['_id']]
        now = query['$or'][1]['notify_until']['$lte']
        if 'notified_at' in row or row.get('notify_until', now) > now:
            return None
        previous = deepcopy(row)
        row.update(update['$set'])
        return previous


class NewUserTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.collection = UsersCollection()
        self.client = SimpleNamespace(send_message=AsyncMock())
        self.user = SimpleNamespace(id=98765, first_name='<Siam> **name**\nInjected',
                                    last_name='Chowdhury', username='siam', language_code='bn',
                                    is_premium=True, is_verified=False, dc_id=4)
        self.channel = patch.object(new_users.Telegram, 'CHANNEL_ID', -1001234567890)
        self.database = patch.object(new_users.db, 'db', {'bot_users': self.collection})
        self.channel.start(); self.database.start()
        self.addCleanup(self.channel.stop); self.addCleanup(self.database.stop)

    async def test_profile_notice_is_plain_text_and_persists_across_restart(self):
        from hydrogram.enums import ParseMode
        await new_users.notify_new_user(self.client, self.user)
        sent = self.client.send_message.call_args.kwargs
        self.assertEqual(sent['chat_id'], -1001234567890)
        self.assertEqual(sent['parse_mode'], ParseMode.DISABLED)
        self.assertTrue(sent['disable_web_page_preview'])
        for text in ('User ID: 98765', 'First name: <Siam> **name**Injected',
                     'Last name: Chowdhury', 'Username: @siam', 'https://t.me/siam',
                     'Language: bn', 'Premium: Yes', 'Verified: No', 'Telegram DC: 4',
                     'First start (UTC):'):
            self.assertIn(text, sent['text'])
        # All state is in MongoDB, not a process-local user set.
        self.collection.rows = deepcopy(self.collection.rows)
        await new_users.notify_new_user(SimpleNamespace(send_message=self.client.send_message), self.user)
        self.client.send_message.assert_awaited_once()
        self.assertEqual(set(self.collection.rows[98765]), {'_id', 'first_seen', 'notified_at'})

    async def test_parallel_starts_send_once_and_expired_claim_recovers(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def send(**kwargs):
            entered.set()
            await release.wait()
        self.client.send_message.side_effect = send
        first = asyncio.create_task(new_users.notify_new_user(self.client, self.user))
        await entered.wait()
        await new_users.notify_new_user(self.client, self.user)
        release.set()
        await first
        self.client.send_message.assert_awaited_once()
        self.collection.rows[98765].pop('notified_at')
        self.collection.rows[98765]['notify_until'] = datetime.now(timezone.utc) - timedelta(seconds=1)
        await new_users.notify_new_user(self.client, self.user)
        self.assertEqual(self.client.send_message.await_count, 2)

    async def test_delivery_failure_retries_without_losing_first_start(self):
        self.client.send_message.side_effect = RuntimeError('no posting permission')
        await new_users.notify_new_user(self.client, self.user)
        first_seen = self.collection.rows[98765]['first_seen']
        self.assertNotIn('notify_until', self.collection.rows[98765])
        self.assertNotIn('notified_at', self.collection.rows[98765])
        self.client.send_message.side_effect = None
        await new_users.notify_new_user(self.client, self.user)
        self.assertIn('notified_at', self.collection.rows[98765])
        self.assertEqual(self.collection.rows[98765]['first_seen'], first_seen)

    async def test_missing_channel_and_database_or_sender_send_nothing(self):
        with patch.object(new_users.Telegram, 'CHANNEL_ID', 0):
            await new_users.notify_new_user(self.client, self.user)
        with patch.object(new_users.db, 'db', None):
            await new_users.notify_new_user(self.client, self.user)
        await new_users.notify_new_user(self.client, None)
        self.client.send_message.assert_not_awaited()
        self.assertFalse(self.collection.rows)

    async def test_minimal_profile_and_database_failure_are_safe(self):
        self.collection.update_one = AsyncMock(side_effect=RuntimeError('unreachable'))
        await new_users.notify_new_user(self.client, self.user)
        self.client.send_message.assert_not_awaited()
        text = new_users.user_notice(SimpleNamespace(id=5), datetime.now(timezone.utc))
        self.assertIn('tg://user?id=5', text)
        self.assertIn('Username: Not set', text)
        self.assertIn('Last name: Not provided', text)

    async def test_start_integration_help_and_user_restriction(self):
        with patch.object(clients.TelegramBot, 'add_handler'):
            from bot.plugins import commands
        message = SimpleNamespace(from_user=self.user, command=['start'], reply=AsyncMock())
        with patch.object(commands, 'notify_new_user', AsyncMock()) as notify:
            with patch.object(commands.Telegram, 'ALLOWED_USER_IDS', []):
                for command in ('start', 'start@TestGateStreamBot'):
                    message.command = [command]
                    await commands.start_command(self.client, message)
                self.assertEqual(notify.await_count, 2)
                notify.assert_awaited_with(self.client, self.user)
                notify.reset_mock()
                message.command = ['help']
                await commands.start_command(self.client, message)
                notify.assert_not_awaited()
            with patch.object(commands.Telegram, 'ALLOWED_USER_IDS', ['1']):
                message.command = ['start']
                await commands.start_command(self.client, message)
                notify.assert_not_awaited()
        self.assertEqual(message.reply.await_count, 3)
