import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from hydrogram import enums, raw
from hydrogram.errors import FloodWait, MessageDeleteForbidden, MessageIdInvalid
from hydrogram.types import Chat, Message, User

with patch.dict(os.environ, {
    'TELEGRAM_API_ID': '12345', 'TELEGRAM_API_HASH': 'test_hash',
    'TELEGRAM_BOT_TOKEN': '12345:test_token', 'TELEGRAM_BOT_USERNAME': 'TestGateStreamBot',
    'OWNER_ID': '12345', 'DATABASE_URL': 'mongodb://localhost:27017',
    'SPACE_ID': 'user/app', 'SPACE_HOST': 'user-app.hf.space',
}, clear=True):
    from bot.clients import TelegramBot
    from bot.modules import live_stats
    from bot.modules.activity import TransferActivity
    with patch.object(TelegramBot, 'add_handler'):
        from bot.plugins import commands


class LiveStatsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dashboard = live_stats.LiveStats()
        self.tracker = TransferActivity()
        self.settings = SimpleNamespace(find_one=AsyncMock(return_value=None), update_one=AsyncMock())
        self.client = SimpleNamespace(
            me=SimpleNamespace(id=12345, username='TestGateStreamBot'),
            delete_messages=AsyncMock(),
            edit_message_text=AsyncMock(),
            executor=None,
            loop=asyncio.get_running_loop(),
        )
        self.enterContext(patch.object(live_stats.lifecycle, 'spawn', asyncio.create_task))
        self.enterContext(patch.object(live_stats, 'activity', self.tracker))
        self.enterContext(patch.object(live_stats, '_host_metrics', return_value='RAM <b>35 MB</b>'))
        self.enterContext(patch.object(live_stats.db, 'db', {'bot_settings': self.settings}))
        self.enterContext(patch.object(live_stats.db, 'direct_files',
                                       SimpleNamespace(count_documents=AsyncMock(return_value=3642))))
        self.addAsyncCleanup(self.stop_dashboard)

    async def stop_dashboard(self):
        if self.dashboard.task:
            self.dashboard.task.cancel()
            await asyncio.gather(self.dashboard.task, return_exceptions=True)

    def request(self, chat_id, message_id):
        return SimpleNamespace(reply=AsyncMock(
            return_value=SimpleNamespace(id=message_id, chat=SimpleNamespace(id=chat_id))))

    async def test_only_owner_can_move_the_dashboard_and_forum_topic_is_preserved(self):
        group = Chat(id=-1000000000042, type=enums.ChatType.SUPERGROUP)
        caller = User(id=12345, first_name='Owner', is_bot=False)
        request = Message(id=11, chat=group, from_user=caller, text='/stats@TestGateStreamBot',
                          message_thread_id=42, client=self.client)
        self.client.send_message = AsyncMock(return_value=Message(id=12, chat=group))
        handlers = commands.stats_command.handlers
        self.assertTrue(await handlers[0][0].filters(self.client, request))

        with patch.object(commands, 'live_stats', self.dashboard):
            await commands.stats_command(self.client, request)
            self.assertEqual(self.client.send_message.await_args.kwargs['message_thread_id'], 42)
            self.assertEqual(self.dashboard.current, (group.id, 12))
            caller.id = 999
            await commands.stats_command(self.client, request)

        self.client.delete_messages.assert_not_awaited()
        self.assertIn('Only the owner', self.client.send_message.await_args.kwargs['text'])

    async def test_rapid_requests_delete_the_old_chat_message_before_posting(self):
        first, second = self.request(12345, 10), self.request(-1000000000042, 20)
        first_started = asyncio.Event()
        finish_first = asyncio.Event()
        order = []

        async def send_first(*args, **kwargs):
            first_started.set()
            await finish_first.wait()
            order.append('first')
            return SimpleNamespace(id=10, chat=SimpleNamespace(id=12345))

        async def delete_old(chat_id, message_id):
            self.assertEqual((chat_id, message_id), (12345, 10))
            order.append('delete')

        async def send_second(*args, **kwargs):
            order.append('second')
            return SimpleNamespace(id=20, chat=SimpleNamespace(id=-1000000000042))

        first.reply.side_effect = send_first
        second.reply.side_effect = send_second
        self.client.delete_messages.side_effect = delete_old
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(self.dashboard.show(self.client, first))
            await first_started.wait()
            tasks.create_task(self.dashboard.show(self.client, second))
            finish_first.set()

        self.assertEqual(order, ['first', 'delete', 'second'])
        self.assertEqual(self.dashboard.current, (-1000000000042, 20))
        self.assertEqual(self.settings.update_one.await_args.args[1]['$set'],
                         {'chat_id': -1000000000042, 'message_id': 20})

    async def test_stats_edit_the_same_message_when_traffic_changes(self):
        sample_changed = asyncio.Event()
        self.client.edit_message_text.side_effect = lambda *args, **kwargs: sample_changed.set()
        request = self.request(12345, 10)
        with patch.object(live_stats, 'REFRESH_SECONDS', 0.01):
            await self.dashboard.show(self.client, request)
            ident = self.tracker.start('media', 200, 'file')
            self.tracker.progress(ident, 100)
            async with asyncio.timeout(1):
                await sample_changed.wait()

        old_message = request.reply.await_args
        initial = await TelegramBot.parser.parse(old_message.args[0], mode=old_message.kwargs['parse_mode'])
        self.assertEqual(sum(isinstance(entity, raw.types.MessageEntityBlockquote)
                             for entity in initial['entities']), 3)
        self.assertIn('3,642 uploads', initial['message'])
        edit = self.client.edit_message_text.await_args
        self.assertEqual(edit.args[:2], (12345, 10))
        self.assertIn('<b>1</b> active links', edit.args[2])
        self.assertIn('<b>100.0 B</b> sent', edit.args[2])
        request.reply.assert_awaited_once()

    async def test_failed_deletion_leaves_the_previous_dashboard_live(self):
        await self.dashboard.show(self.client, self.request(12345, 10))
        previous_task = self.dashboard.task
        self.client.delete_messages.side_effect = MessageDeleteForbidden()
        request = self.request(-1000000000042, 20)
        await self.dashboard.show(self.client, request)

        self.assertIs(self.dashboard.task, previous_task)
        self.assertFalse(previous_task.done())
        self.assertEqual(self.dashboard.current, (12345, 10))
        self.assertIn('Could not replace', request.reply.await_args.args[0])

    async def test_restart_resumes_saved_message_and_handles_a_manual_deletion(self):
        self.settings.find_one.return_value = {'chat_id': -1000000000042, 'message_id': 20}
        self.client.edit_message_text.side_effect = MessageIdInvalid()
        await self.dashboard.restore(self.client)
        async with asyncio.timeout(1):
            await self.dashboard.task
        self.assertEqual(self.client.edit_message_text.await_args.args[:2], (-1000000000042, 20))
        self.client.delete_messages.side_effect = MessageIdInvalid()
        await self.dashboard.show(self.client, self.request(12345, 30))
        self.assertEqual(self.dashboard.current, (12345, 30))

    async def test_respects_telegram_flood_wait(self):
        self.client.edit_message_text.side_effect = FloodWait(19)
        self.settings.find_one.return_value = {'chat_id': 12345, 'message_id': 10}
        with patch('bot.modules.live_stats.asyncio.sleep', AsyncMock(side_effect=asyncio.CancelledError)) as wait:
            await self.dashboard.restore(self.client)
            with self.assertRaises(asyncio.CancelledError):
                await self.dashboard.task
        wait.assert_awaited_once_with(19)
