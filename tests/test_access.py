import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from hydrogram.enums import ChatType
from hydrogram.types import Message

with patch.dict(os.environ, {
    'TELEGRAM_API_ID': '12345', 'TELEGRAM_API_HASH': 'test_hash',
    'TELEGRAM_BOT_TOKEN': '12345:test_token', 'TELEGRAM_BOT_USERNAME': 'TestGateStreamBot',
    'OWNER_ID': '12345', 'DATABASE_URL': 'mongodb://localhost:27017',
    'SPACE_ID': 'user/app', 'SPACE_HOST': 'user-app.hf.space',
}, clear=True):
    from bot.clients import TelegramBot
    from bot.modules import access, bot_menu
    with patch.object(TelegramBot, 'add_handler'):
        from bot.plugins import access as commands, callback, files, personal_index


OWNER = 12345
USER = 9876
GROUP = -1001234


def update(user, command=None, chat=ChatType.PRIVATE, chat_id=None, **fields):
    return SimpleNamespace(from_user=SimpleNamespace(id=user), command=command,
        chat=SimpleNamespace(type=chat, id=chat_id or user), sender_chat=None,
        reply=AsyncMock(), reply_to_message=None, **fields)


class AccessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.grants = set()
        collection = SimpleNamespace(find_one=self.find, update_one=self.add, delete_one=self.remove)
        store = patch.object(access.db, 'db', {'bot_access': collection})
        store.start()
        self.addCleanup(store.stop)

    async def find(self, query, projection):
        return next(({'_id': key} for key in query['_id']['$in'] if key in self.grants), None)

    async def add(self, query, update, *, upsert):
        self.grants.add(query['_id'])

    async def remove(self, query):
        self.grants.discard(query['_id'])

    async def test_only_owner_can_grant_users_and_groups_and_revoke(self):
        self.assertTrue(await access.has_access(OWNER))
        self.assertFalse(await access.has_access(USER))
        outsider = update(USER, ['auth', str(USER)])
        await commands.access_command(None, outsider)
        self.assertFalse(self.grants)
        self.assertIn('Only the owner', outsider.reply.call_args.args[0])

        owner = update(OWNER, ['auth'], chat=ChatType.SUPERGROUP, chat_id=GROUP)
        await commands.access_command(None, owner)
        self.assertEqual(self.grants, {GROUP})
        self.assertTrue(await access.has_access(USER, GROUP))
        self.assertFalse(await access.has_access(USER))
        self.assertFalse(await access.has_access(USER, GROUP - 1))

        owner.reply_to_message = update(USER)
        await commands.access_command(None, owner)
        self.assertTrue(await access.has_access(USER))
        owner.command = ['unauth', str(USER)]
        await commands.access_command(None, owner)
        self.assertFalse(await access.has_access(USER))
        self.assertTrue(await access.has_access(USER, GROUP))
        owner.command, owner.reply_to_message = ['unauth'], None
        await commands.access_command(None, owner)
        self.assertFalse(await access.has_access(USER, GROUP))

    async def test_group_index_and_upload_are_scoped_to_that_chat(self):
        self.grants.add(GROUP)
        message = update(USER, ['index'], chat=ChatType.SUPERGROUP, chat_id=GROUP, text='/index')
        with patch.object(access.Telegram, 'CHANNEL_ID', -1005678), \
             patch.object(personal_index.library, 'active_folder', AsyncMock(return_value=None)) as active, \
             patch.object(personal_index.library, 'create_folder', AsyncMock()) as create:
            await personal_index.index_command(None, message)
            active.assert_awaited_once_with(USER, GROUP)
            create.assert_awaited_once_with(USER, 'GateStream-Index', GROUP)
            self.assertIn('reply to this message', message.reply.call_args.args[0])

        audio = SimpleNamespace(file_name='Courtesy Call.flac', file_size=30300000,
                                mime_type='audio/flac', duration=236)
        copied = SimpleNamespace(id=42, audio=audio, caption=None)
        media = update(USER, chat=ChatType.SUPERGROUP, chat_id=GROUP, audio=audio,
                       copy=AsyncMock(return_value=copied))
        client = SimpleNamespace(me=SimpleNamespace(id=100), loop=asyncio.get_running_loop(), executor=None)
        self.assertTrue(await files.UPLOAD_MEDIA(client, Message(id=42, chat=media.chat, audio=audio)))
        status = SimpleNamespace(edit_text=AsyncMock())
        media.reply.return_value = status

        async def active_folder(user_id, chat_id):
            if (user_id, chat_id) == (USER, GROUP):
                return {'_id': 'group-draft'}
            return None

        with patch.object(access.Telegram, 'CHANNEL_ID', -1005678), \
             patch.object(files.library, 'active_folder', side_effect=active_folder) as active, \
             patch.object(files.library, 'folder_count', AsyncMock(return_value=0)), \
             patch.object(files, 'save_file', AsyncMock(return_value={'code': 'song'})) as save:
            await files.handle_user_file(client, media)
            active.assert_awaited_once_with(USER, GROUP)
            self.assertEqual(save.call_args.kwargs['personal_index_id'], 'group-draft')
            self.assertEqual(save.call_args.kwargs['user_id'], USER)
            self.assertEqual(save.call_args.kwargs['file_name'], audio.file_name)
            self.assertEqual(save.call_args.kwargs['mime_type'], audio.mime_type)
            media.reply.assert_not_called()

            self.grants.add(GROUP - 1)
            media.chat.id = GROUP - 1
            await files.handle_user_file(client, media)
            self.assertEqual(save.await_count, 1)
            media.copy.assert_awaited_once()

            media.reply_to_message = SimpleNamespace(from_user=SimpleNamespace(id=100))
            await files.handle_user_file(client, media)
            self.assertIsNone(save.call_args.kwargs['personal_index_id'])
            status.edit_text.assert_awaited_once()

            self.grants.clear()
            media.chat.id = GROUP
            await files.handle_user_file(client, media)
            self.assertEqual(media.copy.await_count, 2)

    async def test_menu_callbacks_obey_chat_access_and_owner_permissions(self):
        self.grants.add(GROUP)
        message = update(OWNER, chat=ChatType.GROUP, chat_id=GROUP)
        query = SimpleNamespace(from_user=SimpleNamespace(id=USER), data='menu:index',
                                message=message, answer=AsyncMock())
        with patch.object(personal_index, 'personal_index', AsyncMock()) as index:
            await callback.manage_callback(None, query)
            index.assert_awaited_once_with(message, user_id=USER)
            query.data = 'menu:access'
            await callback.manage_callback(None, query)
            self.assertTrue(query.answer.call_args.kwargs['show_alert'])
            self.assertIn('unavailable', query.answer.call_args.args[0])
            query.data = 'menu:index'
            message.chat.type, message.chat.id = ChatType.PRIVATE, USER
            await callback.manage_callback(None, query)
            index.assert_awaited_once()
            self.assertIn('has not granted', query.answer.call_args.args[0])

        await bot_menu.send_menu(message, OWNER)
        buttons = message.reply.call_args.kwargs['reply_markup'].inline_keyboard
        self.assertEqual([button.text for row in buttons for button in row],
                         ['Index', 'Library', 'My ID', 'Manage access', 'Set up worker repair'])

    async def test_access_fails_closed_when_mongo_is_unavailable(self):
        with patch.object(access.db, 'db', None):
            self.assertTrue(await access.has_access(OWNER))
            self.assertFalse(await access.has_access(USER, GROUP))
