import os
import re
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

with patch.dict(os.environ, {
    'TELEGRAM_API_ID': '12345', 'TELEGRAM_API_HASH': 'test_hash',
    'TELEGRAM_BOT_TOKEN': '12345:test_token', 'TELEGRAM_BOT_USERNAME': 'TestGateStreamBot',
    'OWNER_ID': '12345', 'DATABASE_URL': 'mongodb://localhost:27017',
    'SPACE_ID': 'user/app', 'SPACE_HOST': 'user-app.hf.space',
}, clear=True):
    from bot.server import instance, catalog
    from bot.modules import index_access
    from bot import clients


class SettingsCollection:
    def __init__(self):
        self.document = None

    async def find_one(self, query):
        return self.document

    async def find_one_and_update(self, query, update, **kwargs):
        if self.document is None:
            self.document = {'_id': 'index_access', **update['$setOnInsert']}
        return self.document

    async def update_one(self, query, update, **kwargs):
        self.document = {'_id': 'index_access', **(self.document or {}), **update['$set']}


class IndexAccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_password_persists_and_owner_changes_revoke_old_sessions(self):
        collection = SettingsCollection()
        with patch.object(index_access, '_collection', return_value=collection):
            original = await index_access.settings()
            self.assertNotIn('Siam', original['password_hash'])
            self.assertTrue(await index_access.verify_password('Siam', original))
            self.assertFalse(await index_access.verify_password('siam', original))
            token = index_access.session_token(original)
            self.assertTrue(index_access.valid_session(token, original))
            self.assertFalse(index_access.valid_session(token + 'tampered', original))
            await index_access.set_password('New secret')
            changed = await index_access.settings()
            self.assertFalse(index_access.valid_session(token, changed))
            self.assertTrue(await index_access.verify_password('New secret', changed))
            self.assertFalse(await index_access.verify_password('Siam', changed))
            self.assertEqual((await index_access.settings())['password_hash'], changed['password_hash'])

    async def test_sessions_expire(self):
        document = index_access._record('Siam')
        with patch('itsdangerous.timed.TimestampSigner.get_timestamp', return_value=100):
            token = index_access.session_token(document)
        with patch('itsdangerous.timed.TimestampSigner.get_timestamp', return_value=101 + index_access.SESSION_AGE):
            self.assertFalse(index_access.valid_session(token, document))


class IndexRouteTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = index_access._record('Siam')

    async def asyncSetUp(self):
        catalog._attempts.clear()
        self.client = instance.test_client()
        self.settings_patch = patch.object(index_access, 'settings', AsyncMock(return_value=dict(self.document)))
        self.settings = self.settings_patch.start()
        self.addCleanup(self.settings_patch.stop)

    async def csrf(self):
        response = await self.client.get('/index?section=link', scheme='https')
        body = await response.get_data(as_text=True)
        return re.search(r'name="csrf" value="([^"]+)"', body).group(1)

    async def login(self, password='Siam'):
        token = await self.csrf()
        return await self.client.post('/index/login', form={'csrf': token, 'password': password}, scheme='https')

    async def test_unauthenticated_page_never_queries_or_exposes_files(self):
        with patch.object(catalog, 'list_files', AsyncMock()) as listing:
            response = await self.client.get('/index?section=link', scheme='https')
            listing.assert_not_awaited()
        body = await response.get_data(as_text=True)
        self.assertIn('Unlock Link', body)
        self.assertNotIn('Siam', body)
        self.assertIn('no-store', response.headers['Cache-Control'])
        api = await self.client.get('/api/movies', scheme='https')
        self.assertEqual(api.status_code, 401)

    async def test_public_index_does_not_need_password_or_settings(self):
        data = dict(files=[], total=0, page=1, pages=1)
        with patch.object(catalog, 'list_files', AsyncMock(return_value=data)) as listing:
            response = await self.client.get('/index', scheme='https')
            listing.assert_awaited_once_with(1, '', 'all', 'index')
        self.settings.assert_not_awaited()
        self.assertEqual(response.status_code, 200)
        body = await response.get_data(as_text=True)
        self.assertIn('The public index.', body)
        self.assertNotIn('type="password"', body)
        self.assertIn('section=link', body)

    async def test_bulk_download_access_validation_and_zip_contents(self):
        import io
        from zipfile import ZipFile
        from quart import Response
        from werkzeug.datastructures import MultiDict
        from bot.server import main
        from urllib.parse import urlencode
        data = dict(files=[], total=0, page=1, pages=1)
        with patch.object(catalog, 'list_files', AsyncMock(return_value=data)):
            response = await self.client.get('/index', scheme='https')
        csrf = re.search(r'name="csrf" value="([^"]+)"', await response.get_data(as_text=True)).group(1)
        form = MultiDict([('csrf', csrf), ('section', 'index'), ('code', 'episode1'), ('code', 'episode2')])
        async def post():
            return await self.client.post('/index/download', data=urlencode(list(form.items(multi=True))),
                headers={'Content-Type': 'application/x-www-form-urlencoded'}, scheme='https')
        files = [dict(code='episode1', file_name='../same.mp4'), dict(code='episode2', file_name='../same.mp4')]

        async def download(code):
            async def body():
                yield b'ab'
                yield b'cd'
            return Response(body(), headers={'Content-Length': '4'})

        with patch.object(catalog, 'selected_files', AsyncMock(return_value=files)) as selected:
            with patch.object(main, 'transmit_file', download):
                response = await post()
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content_type, 'application/zip')
                selected.assert_awaited_once_with(['episode1', 'episode2'], 'index')
                with ZipFile(io.BytesIO(await response.get_data())) as archive:
                    self.assertEqual(archive.namelist(), ['01-.._same.mp4', '02-.._same.mp4'])
                    self.assertEqual([archive.read(name) for name in archive.namelist()], [b'abcd', b'abcd'])
            form['section'] = 'link'
            selected.reset_mock()
            self.assertEqual((await post()).status_code, 401)
            selected.assert_not_awaited()
            form['section'] = 'index'
            selected.return_value = []  # A private code cannot be smuggled into public selection.
            self.assertEqual((await post()).status_code, 409)
            form.setlist('code', ['x'] * 31 + [str(i) for i in range(31)])
            self.assertEqual((await post()).status_code, 400)
            form.setlist('code', ['../../bad'])
            self.assertEqual((await post()).status_code, 400)
            form.setlist('code', ['episode1'])
            form['csrf'] = 'wrong'
            self.assertEqual((await post()).status_code, 400)
        await self.login()
        form['csrf'] = await self.csrf()
        form['section'] = 'link'
        with patch.object(catalog, 'selected_files', AsyncMock(return_value=[files[0]])), patch.object(main, 'transmit_file', download):
            response = await post()
            self.assertEqual(response.status_code, 200)
            with ZipFile(io.BytesIO(await response.get_data())) as archive:
                self.assertEqual(archive.read(archive.namelist()[0]), b'abcd')

    async def test_login_cookie_catalog_render_filters_and_logout(self):
        response = await self.login()
        self.assertEqual(response.status_code, 302)
        cookie = '\n'.join(response.headers.getlist('Set-Cookie'))
        for flag in ['HttpOnly', 'Secure', 'SameSite=Lax']:
            self.assertIn(flag, cookie)
        data = dict(files=[dict(code='test-code', file_name='<script>alert(1)</script>.mp4',
            file_size=1024, category='anime', duration_formatted='01:00', batch_title='Season 1')],
            total=61, page=2, pages=3)
        with patch.object(catalog, 'list_files', AsyncMock(return_value=data)) as listing:
            response = await self.client.get('/index?section=link&q=title&category=anime&page=2', scheme='https')
            listing.assert_awaited_once_with(2, 'title', 'anime', 'link')
        body = await response.get_data(as_text=True)
        self.assertIn('/dl/test-code', body)
        self.assertIn('Download', body)
        self.assertIn('Season 1', body)
        self.assertIn('&lt;script&gt;', body)
        self.assertNotIn('<script>alert(1)</script>', body)
        self.assertIn('Page 2 of 3', body)
        self.assertIn('page=3', body)
        csrf = re.search(r'name="csrf" value="([^"]+)"', body).group(1)
        self.assertEqual((await self.client.post('/index/logout', form={'csrf': csrf}, scheme='https')).status_code, 302)
        body = await (await self.client.get('/index?section=link', scheme='https')).get_data(as_text=True)
        self.assertIn('Unlock Link', body)

    async def test_wrong_password_csrf_failures_and_database_failure_are_closed(self):
        self.assertEqual((await self.login('wrong')).status_code, 401)
        response = await self.client.post('/index/login', form={'password': 'Siam'}, scheme='https')
        self.assertEqual(response.status_code, 400)
        token = await self.csrf()
        with patch.object(index_access, 'settings', AsyncMock(side_effect=RuntimeError('offline'))):
            response = await self.client.post('/index/login', form={'csrf': token, 'password': 'Siam'}, scheme='https')
        self.assertEqual(response.status_code, 503)

    async def test_rate_limiting(self):
        token = await self.csrf()
        with patch.object(index_access, 'verify_password', AsyncMock(return_value=False)) as verify:
            for _ in range(10):
                response = await self.client.post('/index/login', form={'csrf': token, 'password': 'wrong'}, scheme='https')
                self.assertEqual(response.status_code, 401)
            response = await self.client.post('/index/login', form={'csrf': token, 'password': 'wrong'}, scheme='https')
            self.assertEqual(response.status_code, 429)
            self.assertEqual(verify.await_count, 10)

    async def test_password_change_invalidates_browser_session(self):
        await self.login()
        self.settings.return_value = index_access._record('Another password')
        with patch.object(catalog, 'list_files', AsyncMock()) as listing:
            response = await self.client.get('/index?section=link', scheme='https')
            listing.assert_not_awaited()
        self.assertIn('Unlock Link', await response.get_data(as_text=True))


class OwnerPasswordTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        with patch.object(clients.TelegramBot, 'add_handler'):
            from bot.plugins import index_password
        cls.plugin = index_password

    async def test_only_owner_can_save_password_and_secret_is_not_echoed(self):
        from hydrogram.types import Message
        message = MagicMock(spec=Message)
        message.from_user = SimpleNamespace(id=999)
        message.text = '/setpassword New secret'
        message.reply = AsyncMock()
        message.delete = AsyncMock()
        with patch.object(self.plugin, 'set_password', AsyncMock()) as save:
            await self.plugin.index_password_command(None, message)
            save.assert_not_awaited()
            self.assertIn('owner', message.reply.call_args.args[0])
            message.from_user.id = 12345
            await self.plugin.index_password_command(None, message)
            save.assert_awaited_once_with('New secret')
            self.assertNotIn('New secret', message.reply.call_args.args[0])
            message.delete.assert_awaited_once()
