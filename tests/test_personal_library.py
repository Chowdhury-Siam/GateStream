import os
import re
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

with patch.dict(os.environ, {
    'TELEGRAM_API_ID': '12345', 'TELEGRAM_API_HASH': 'test_hash',
    'TELEGRAM_BOT_TOKEN': '12345:test_token', 'TELEGRAM_BOT_USERNAME': 'TestGateStreamBot',
    'OWNER_ID': '12345', 'DATABASE_URL': 'mongodb://localhost:27017',
    'SPACE_ID': 'user/app', 'SPACE_HOST': 'user-app.hf.space',
}, clear=True):
    from bot.modules import personal_library as library
    from bot.server import instance


async def csrf(response):
    return re.search(r'name="csrf" value="([^"]+)"', await response.get_data(as_text=True)).group(1)


class PersonalLibraryTests(unittest.IsolatedAsyncioTestCase):
    async def test_telegram_login_is_one_time_and_isolates_browser_accounts(self):
        sessions = {}

        async def insert(row):
            sessions[row['_id']] = row

        async def find(query):
            row = sessions.get(query['_id'])
            if row and row['kind'] == query['kind'] and row['expires_at'] > query['expires_at']['$gt']:
                return row

        async def consume(query):
            row = await find(query)
            return sessions.pop(query['_id']) if row else None

        async def delete(query):
            sessions.pop(query['_id'], None)

        storage = SimpleNamespace(insert_one=insert, find_one=find, find_one_and_delete=consume, delete_one=delete)
        first, other = instance.test_client(), instance.test_client()
        names = {111: 'first-user.apk', 222: 'second-user.pdf'}

        async def listing(user_id, *args):
            return [dict(code=str(user_id), file_name=names[user_id], file_size=6)], 1, 1, 1

        grants = {111, 222}

        async def find_grant(query, projection):
            return next(({'_id': ident} for ident in query['_id']['$in'] if ident in grants), None)

        with patch.object(library.db, 'db', {'library_sessions': storage,
                                             'bot_access': SimpleNamespace(find_one=find_grant)}), \
             patch.object(library, 'page_items', side_effect=listing) as get_items:
            root = await first.get('/index', scheme='https')
            self.assertIn('Open Telegram bot', await root.get_data(as_text=True))
            self.assertIn('no-store', root.headers['Cache-Control'])
            get_items.assert_not_called()
            for user_id, browser in ((111, first), (222, other)):
                message = SimpleNamespace(from_user=SimpleNamespace(id=user_id), reply=AsyncMock())
                await library.send_login_link(message)
                link = message.reply.call_args.kwargs['reply_markup'].inline_keyboard[0][0].url
                path, token = link[link.index('/index/'):].split('#')
                self.assertNotIn(token, sessions)
                form = await browser.get(path, scheme='https')
                values = {'csrf': await csrf(form), 'token': token}
                self.assertEqual((await browser.post('/index/sign-in', form=values, scheme='https')).status_code, 303)
                self.assertEqual((await browser.post('/index/sign-in', form=values, scheme='https')).status_code, 400)
                response = await browser.get('/index', scheme='https')
                body = await response.get_data(as_text=True)
                self.assertIn(names[user_id], body)
                self.assertNotIn(names[333 - user_id], body)

            own_page = await first.get('/index', scheme='https')
            self.assertEqual((await first.post('/index/sign-out', form={'csrf': await csrf(own_page)},
                                              scheme='https')).status_code, 303)
            signed_out = await first.get('/index', scheme='https')
            self.assertNotIn(names[111], await signed_out.get_data(as_text=True))
            grants.remove(222)
            revoked = await other.get('/index', scheme='https')
            self.assertNotIn(names[222], await revoked.get_data(as_text=True))

    async def test_old_folder_files_appear_as_individual_downloads(self):
        item = dict(code='old-file', file_name='Episode.mkv', file_size=6,
                    mime_type='video/x-matroska', personal_index_id='legacy-folder')
        cursor = SimpleNamespace(to_list=AsyncMock(return_value=[item]))
        cursor.sort = cursor.skip = cursor.limit = lambda *args: cursor

        def find(query):
            self.assertEqual(query['user_id'], 111)
            self.assertNotIn('personal_index_id', query)
            return cursor

        db = SimpleNamespace(count_documents=AsyncMock(return_value=1), find=find)
        with patch.object(library, 'session_user', AsyncMock(return_value=111)), \
             patch.object(library.db, 'direct_files', db):
            response = await instance.test_client().get('/index', scheme='https')
        self.assertEqual(response.status_code, 200)
        body = await response.get_data(as_text=True)
        self.assertIn('Episode.mkv', body)
        self.assertIn('/dl/old-file', body)
        self.assertIn('/stream/old-file', body)
        self.assertNotIn('Download ZIP', body)

    async def test_batch_routes_and_old_folder_links_are_gone(self):
        client = instance.test_client()
        old_id = 'x' * 43
        for path in (f'/index/archive/{old_id}/old.zip', f'/index/archive/{old_id}/status',
                     f'/index/prepare/{old_id}', f'/index/share/{old_id}', f'/index/folder/{old_id}'):
            with self.subTest(path=path):
                self.assertEqual((await client.get(path)).status_code, 404)
        self.assertEqual((await client.post(f'/index/share/{old_id}')).status_code, 404)
