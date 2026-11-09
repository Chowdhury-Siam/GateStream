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
TOPIC = 42


def update(user, command=None, chat=ChatType.PRIVATE, chat_id=None, **fields):
    return SimpleNamespace(from_user=SimpleNamespace(id=user), command=command,
        chat=SimpleNamespace(type=chat, id=chat_id or user), sender_chat=None,
        reply=AsyncMock(), reply_to_message=None, **fields)


class AccessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.grants = set()
        collection = SimpleNamespace(find_one=self.find, update_one=self.add, delete_one=self.remove)
        self.topic = None
        settings = SimpleNamespace(find_one=self.find_topic, update_one=self.set_topic)
        store = patch.object(access.db, 'db', {'bot_access': collection, 'bot_settings': settings})
        store.start()
        self.addCleanup(store.stop)

    async def find(self, query, projection):
        return next(({'_id': key} for key in query['_id']['$in'] if key in self.grants), None)

    async def add(self, query, update, *, upsert):
        self.grants.add(query['_id'])

    async def remove(self, query):
        self.grants.discard(query['_id'])

    async def find_topic(self, query, projection):
        return self.topic if query == self.topic else None

    async def set_topic(self, key, update, *, upsert):
        self.topic = {**key, **update['$set']}

    async def test_only_owner_can_set_the_file_topic(self):
        outsider = update(USER, ['filetolink'], chat=ChatType.SUPERGROUP, chat_id=GROUP, message_thread_id=TOPIC)
        await commands.file_to_link_command(None, outsider)
        self.assertIsNone(self.topic)

        owner = update(OWNER, ['filetolink'], chat=ChatType.SUPERGROUP, chat_id=GROUP, message_thread_id=1)
        await commands.file_to_link_command(None, owner)
        self.assertIsNone(self.topic)
        owner.message_thread_id = TOPIC
        await commands.file_to_link_command(None, owner)
        self.assertEqual(self.topic, {'_id': 'file_to_link', 'chat_id': GROUP, 'topic_id': TOPIC})
        self.assertTrue(await access.is_file_topic(owner))
        owner.message_thread_id = TOPIC + 1
        self.assertFalse(await access.is_file_topic(owner))

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

    async def test_group_files_link_automatically_and_index_only_in_the_bound_topic(self):
        self.grants.update({GROUP, GROUP - 1})
        self.topic = {'_id': 'file_to_link', 'chat_id': GROUP, 'topic_id': TOPIC}
        draft = None
        saved = {}
        audio = SimpleNamespace(file_name='Courtesy Call.flac', file_size=30300000,
                                mime_type='audio/flac', duration=236)
        copied = SimpleNamespace(id=200, audio=audio, caption=None)

        def in_topic(command=None, **fields):
            return update(USER, command, chat=ChatType.SUPERGROUP, chat_id=GROUP,
                          message_thread_id=TOPIC, **fields)

        async def active_folder(user_id, chat_id, *, topic_id=None):
            self.assertEqual((user_id, chat_id, topic_id), (USER, GROUP, TOPIC))
            return draft

        async def create_folder(user_id, name, chat_id, *, topic_id=None):
            nonlocal draft
            self.assertEqual((user_id, chat_id, topic_id), (USER, GROUP, TOPIC))
            draft = {'_id': 'topic-draft', 'name': name}

        async def folder_count(user_id, folder_id):
            return sum(doc['personal_index_id'] == folder_id and doc['user_id'] == user_id
                       for doc in saved.values())

        async def save_file(**fields):
            doc = {**fields, 'code': 'song'}
            saved[(fields['source_key'], fields['user_id'])] = doc
            return doc

        media = in_topic(id=43, audio=audio, copy=AsyncMock(return_value=copied))
        client = SimpleNamespace(loop=asyncio.get_running_loop(), executor=None)
        self.assertTrue(await files.UPLOAD_MEDIA(client, Message(id=43, chat=media.chat, audio=audio)))
        status = SimpleNamespace(edit_text=AsyncMock())
        media.reply.return_value = status
        index = in_topic(['index'], text='/index')
        link = in_topic(['link'], text='/link')
        link.reply_to_message = media

        with patch.object(access.Telegram, 'CHANNEL_ID', -1005678), \
             patch.multiple(files.library, active_folder=active_folder, create_folder=create_folder,
                            folder_count=folder_count, finish_folder=AsyncMock()), \
             patch.object(personal_index, 'reply_zip', AsyncMock()) as reply_zip, \
             patch.object(files, 'get_group_upload', AsyncMock(side_effect=lambda *key: saved.get(key))), \
             patch.object(files, 'save_file', AsyncMock(side_effect=save_file)) as save:
            for item in (media, index, link):
                item.message_thread_id = TOPIC + 1
            await files.handle_user_file(client, media)
            await personal_index.index_command(client, index)
            await files.group_link_command(client, link)
            media.copy.assert_not_awaited()
            for item in (media, index, link):
                item.reply.assert_not_awaited()
                item.message_thread_id = TOPIC

            media.chat.id = GROUP - 1
            await files.handle_user_file(client, media)
            media.copy.assert_not_awaited()
            media.chat.id = GROUP

            await files.handle_user_file(client, media)
            self.assertIsNone(save.call_args.kwargs['personal_index_id'])
            self.assertEqual(save.call_args.kwargs['user_id'], USER)
            self.assertEqual(save.call_args.kwargs['mime_type'], audio.mime_type)
            self.assertIn('/dl/song', status.edit_text.call_args.kwargs['text'])
            await files.group_link_command(client, link)
            self.assertIn('/dl/song', link.reply.call_args.args[0])
            media.copy.assert_awaited_once()

            media.message_thread_id = TOPIC + 1
            await files.group_link_command(client, link)
            self.assertIn('one of your files in this topic', link.reply.call_args.args[0])
            media.message_thread_id = TOPIC

            await personal_index.index_command(client, index)
            self.assertIn('Post your files in this topic', index.reply.call_args.args[0])
            media.id = 44
            media.reply.reset_mock()
            await files.handle_user_file(client, media)
            self.assertEqual(save.call_args.kwargs['personal_index_id'], 'topic-draft')
            self.assertEqual(save.call_args.kwargs['file_name'], audio.file_name)
            media.reply.assert_not_awaited()

            index.text = '/index Songs'
            index.message_thread_id = TOPIC + 1
            await personal_index.index_command(client, index)
            reply_zip.assert_not_awaited()
            index.message_thread_id = TOPIC
            await personal_index.index_command(client, index)
            reply_zip.assert_awaited_once_with(index, {'_id': 'topic-draft', 'name': 'Songs'})

            self.grants.clear()
            media.id = 45
            await files.handle_user_file(client, media)
            self.assertEqual(media.copy.await_count, 2)

    async def test_menu_callbacks_obey_chat_access_and_owner_permissions(self):
        self.grants.add(GROUP)
        self.topic = {'_id': 'file_to_link', 'chat_id': GROUP, 'topic_id': TOPIC}
        message = update(OWNER, chat=ChatType.SUPERGROUP, chat_id=GROUP, message_thread_id=TOPIC + 1)
        query = SimpleNamespace(from_user=SimpleNamespace(id=USER), data='menu:index',
                                message=message, answer=AsyncMock())
        with patch.object(personal_index, 'personal_index', AsyncMock()) as index:
            await callback.manage_callback(None, query)
            index.assert_not_awaited()
            self.assertIn('File To Link', query.answer.call_args.args[0])
            await bot_menu.send_menu(message, OWNER)
            buttons = message.reply.call_args.kwargs['reply_markup'].inline_keyboard
            self.assertNotIn('Index', [button.text for row in buttons for button in row])
            message.message_thread_id = TOPIC
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
