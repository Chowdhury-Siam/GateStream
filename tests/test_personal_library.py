import asyncio
import io
import os
import re
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zipfile import ZipFile

with patch.dict(os.environ, {
    'TELEGRAM_API_ID': '12345', 'TELEGRAM_API_HASH': 'test_hash',
    'TELEGRAM_BOT_TOKEN': '12345:test_token', 'TELEGRAM_BOT_USERNAME': 'TestGateStreamBot',
    'OWNER_ID': '12345', 'DATABASE_URL': 'mongodb://localhost:27017',
    'SPACE_ID': 'user/app', 'SPACE_HOST': 'user-app.hf.space',
}, clear=True):
    from bot import clients
    from bot.modules import index_zip, personal_library as library
    from bot.modules.archive_cache import ArchiveCache
    from bot.server import catalog, instance, main
    from quart import Response
    with patch.object(clients.TelegramBot, 'add_handler'):
        from bot.plugins import callback, channel_index, files


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
        folder_id = 'a' * 43
        folder = {'_id': folder_id, 'user_id': 111, 'name': 'My files'}
        names = {111: 'first-user.apk', 222: 'second-user.pdf'}

        async def listing(user_id, tab, *args):
            self.assertEqual(tab, 'single')
            return [dict(code=str(user_id), file_name=names[user_id], file_size=6)], 1, 1, 1

        async def folder_lookup(requested, user_id):
            return folder if requested == folder_id and user_id == 111 else None

        async def rename(requested, user_id, name):
            if await folder_lookup(requested, user_id):
                folder['name'] = name
                return True
            return False

        with patch.object(library.db, 'db', {'library_sessions': storage}), \
             patch.object(library.Telegram, 'ALLOWED_USER_IDS', []), \
             patch.object(library, 'page_items', side_effect=listing) as get_items, \
             patch.object(library, 'folder_by_id', side_effect=folder_lookup), \
             patch.object(library, 'rename_folder', side_effect=rename), \
             patch.object(library, 'folder_files', AsyncMock(return_value=[])):
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
                response = await browser.get('/index?tab=single', scheme='https')
                body = await response.get_data(as_text=True)
                self.assertIn(names[user_id], body)
                self.assertNotIn(names[333 - user_id], body)

            folder_path = f'/index/folder/{folder_id}'
            self.assertEqual((await other.get(folder_path, scheme='https')).status_code, 404)
            other_page = await other.get('/index?tab=single', scheme='https')
            response = await other.post(folder_path + '/rename', scheme='https',
                                        form={'csrf': await csrf(other_page), 'name': 'Other name'})
            self.assertEqual(response.status_code, 404)
            own_folder = await first.get(folder_path, scheme='https')
            response = await first.post(folder_path + '/rename', scheme='https',
                                        form={'csrf': await csrf(own_folder), 'name': 'Renamed.zip'})
            self.assertEqual(response.status_code, 303)
            self.assertEqual(folder['name'], 'Renamed')
            self.assertEqual((await first.post('/index/sign-out', form={'csrf': await csrf(own_folder)},
                                              scheme='https')).status_code, 303)
            self.assertEqual((await first.get(folder_path, scheme='https')).status_code, 302)

    async def test_bot_collects_fifty_uploads_and_returns_named_folder_link(self):
        folder_id = 'a' * 43
        draft = None
        uploads = []

        async def start(user_id, name):
            nonlocal draft
            draft = dict(_id=folder_id, user_id=user_id, name=name)

        async def active(user_id):
            return draft if draft and draft['user_id'] == user_id else None

        async def finish(folder, name):
            nonlocal draft
            self.assertEqual(folder['_id'], folder_id)
            self.assertEqual(name, 'My fifty files')
            draft = None

        async def save(**item):
            uploads.append(item)
            return {'code': f'upload-{len(uploads)}'}

        async def count(user_id, target):
            return sum(item['personal_index_id'] == target and item['user_id'] == user_id for item in uploads)

        def message(user_id, text=None):
            return SimpleNamespace(from_user=SimpleNamespace(id=user_id), text=text, reply_to_message=None,
                reply=AsyncMock(return_value=SimpleNamespace(edit_text=AsyncMock())), video=None, document=None,
                copy=AsyncMock(return_value=SimpleNamespace(id=42)))

        with patch.object(library.Telegram, 'CHANNEL_ID', -1001234), \
             patch.multiple(library, create_folder=start, active_folder=active, finish_folder=finish, folder_count=count), \
             patch.object(files, 'get_file_properties', return_value=('File.apk', 10, 'application/octet-stream', 0, 'N/A')), \
             patch.object(files, 'save_file', side_effect=save):
            await channel_index.index_command(None, message(111, '/index'))
            await asyncio.gather(*(files.handle_user_file(None, message(111)) for _ in range(50)),
                                 files.handle_user_file(None, message(222)))
            close = message(111, '/index My fifty files')
            await channel_index.index_command(None, close)
            await files.handle_user_file(None, message(111))
        self.assertEqual(sum(item['personal_index_id'] == folder_id for item in uploads), 50)
        self.assertEqual([item['user_id'] for item in uploads if not item['personal_index_id']], [222, 111])
        self.assertTrue(all(item['personal'] for item in uploads))
        self.assertIn('Saved 50 files', close.reply.call_args.args[0])
        buttons = close.reply.call_args.kwargs['reply_markup'].inline_keyboard[0]
        self.assertEqual([(b.text, b.callback_data) for b in buttons], [
            ('Start immediately', f'zip:stream:{folder_id}'),
        ])

    async def test_telegram_zip_starts_before_the_archive_is_complete(self):
        from urllib.parse import urlsplit
        from hydrogram.enums import ChatType

        folder_id = 'a' * 43
        folder = dict(_id=folder_id, user_id=111, name='My ZIP')
        members = [dict(code='one', file_name='File.apk', file_size=4)]
        with tempfile.TemporaryDirectory() as directory:
            cache = ArchiveCache(directory, 1024 * 1024)
            tickets = {}
            release, source_open = asyncio.Event(), asyncio.Event()

            async def insert(ticket):
                tickets[ticket['_id']] = ticket

            async def source(code, **options):
                self.assertTrue(options['internal'])
                async def body():
                    yield b'PK'
                    source_open.set()
                    await release.wait()
                    yield b'12'
                return Response(body(), headers={'Content-Length': '4'})

            message = SimpleNamespace(chat=SimpleNamespace(type=ChatType.PRIVATE),
                edit_text=AsyncMock())
            query = SimpleNamespace(data=f'zip:stream:{folder_id}', message=message,
                from_user=SimpleNamespace(id=111), answer=AsyncMock())
            storage = SimpleNamespace(insert_one=insert, find_one=AsyncMock(side_effect=lambda q: tickets.get(q['_id'])))
            task = None
            try:
                with patch.object(catalog, 'archive_cache', cache), \
                     patch.object(library.db, 'db', {'download_tickets': storage}), \
                     patch.object(library, 'folder_by_id', AsyncMock(return_value=folder)) as lookup, \
                     patch.object(library, 'folder_files', AsyncMock(return_value=members)), \
                     patch.object(main, 'transmit_file', side_effect=source):
                    await callback.manage_callback(None, query)
                    lookup.assert_awaited_once_with(folder_id, 111)
                    query.answer.assert_awaited_once()
                    task = index_zip._jobs[(111, folder_id)]
                    await asyncio.wait_for(source_open.wait(), 1)
                    await asyncio.wait_for(task, 1)
                    message.edit_text.assert_awaited_once()
                    self.assertEqual(len(tickets), 1)
                    token = next(iter(tickets))
                    text, = message.edit_text.call_args.args
                    button = message.edit_text.call_args.kwargs['reply_markup'].inline_keyboard[0][0]
                    self.assertIn(button.url, text)
                    path = urlsplit(button.url).path
                    self.assertEqual(path, f'/index/archive/{token}/My%20ZIP.zip')
                    self.assertFalse(cache.jobs[token]['task'].done())
                    part = await asyncio.wait_for(instance.test_client().get(path, headers={'Range': 'bytes=0-1'}), 1)
                    self.assertEqual(part.status_code, 206)
                    self.assertEqual(await part.get_data(), b'PK')
                    release.set()
                    await asyncio.wait_for(cache.jobs[token]['task'], 1)
                    response = await instance.test_client().get(path)
                    with ZipFile(io.BytesIO(await response.get_data())) as archive:
                        self.assertEqual(archive.read('01-File.apk'), b'PK12')
            finally:
                if task and not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                await cache.close()

    async def test_telegram_zip_choice_rejects_other_users_folder(self):
        from hydrogram.enums import ChatType

        folder_id = 'a' * 43
        query = SimpleNamespace(data=f'zip:prepare:{folder_id}', from_user=SimpleNamespace(id=222),
            message=SimpleNamespace(chat=SimpleNamespace(type=ChatType.PRIVATE)), answer=AsyncMock())
        with patch.object(library, 'folder_by_id', AsyncMock(return_value=None)) as lookup, \
             patch.object(catalog, 'create_archive', AsyncMock()) as create:
            await callback.manage_callback(None, query)
            lookup.assert_not_awaited()
            self.assertIn('Start immediately', query.answer.call_args.args[0])
            query.data = f'zip:stream:{folder_id}'
            await callback.manage_callback(None, query)
        lookup.assert_awaited_once_with(folder_id, 222)
        self.assertTrue(query.answer.call_args.kwargs['show_alert'])
        create.assert_not_awaited()

    async def test_shared_folder_zip_can_resume_without_library_session(self):
        folder_id = 'a' * 43
        folder = {'_id': folder_id, 'user_id': 111, 'name': 'My fifty files'}
        members = [dict(code=str(i), file_name=f'File-{i}.apk', file_size=4) for i in range(50)]
        tickets = {}

        async def insert(ticket):
            tickets[ticket['_id']] = ticket

        async def source(code, **options):
            self.assertTrue(options['internal'])
            return Response(b'PK' + int(code).to_bytes(2))

        store = SimpleNamespace(insert_one=insert, find_one=AsyncMock(side_effect=lambda q: tickets.get(q['_id'])))
        browser, manager = instance.test_client(), instance.test_client()
        with tempfile.TemporaryDirectory() as directory:
            cache = ArchiveCache(directory, 1024 * 1024)
            try:
                with patch.object(catalog, 'archive_cache', cache), \
                     patch.object(library.db, 'db', {'download_tickets': store}), \
                     patch.object(library, 'folder_by_id', AsyncMock(return_value=folder)), \
                     patch.object(library, 'folder_count', AsyncMock(return_value=50)), \
                     patch.object(library, 'folder_files', AsyncMock(return_value=members)) as owned, \
                     patch.object(main, 'transmit_file', side_effect=source):
                    path = f'/index/share/{folder_id}'
                    landing = await browser.get(path, scheme='https')
                    html = await landing.get_data(as_text=True)
                    self.assertIn('50 files', html)
                    self.assertNotIn('Prepare first', html)
                    self.assertEqual(tickets, {})
                    response = await browser.post(path, form={'csrf': await csrf(landing)}, scheme='https')
                    self.assertEqual(response.status_code, 303)
                    token = next(iter(tickets))
                    await asyncio.wait_for(cache.jobs[token]['task'], 2)
                    self.assertEqual(tickets[token]['mode'], 'stream')
                    link = response.headers['Location']
                    result = await manager.get(link, scheme='https')
                    self.assertEqual(result.status_code, 200)
                    self.assertIn('My fifty files.zip', result.headers['Content-Disposition'])
                    with ZipFile(io.BytesIO(await result.get_data())) as archive:
                        self.assertEqual(len(archive.namelist()), 50)
                        self.assertIsNone(archive.testzip())
                        self.assertEqual(archive.read('50-File-49.apk'), b'PK\x001')
                    part = await manager.get(link, scheme='https', headers={'Range': 'bytes=0-3'})
                    self.assertEqual(part.status_code, 206)
                    self.assertEqual(await part.get_data(), b'PK\x03\x04')
                    self.assertEqual(owned.call_args.args, (111, folder_id))
            finally:
                await cache.close()
