import os
import re
import unittest
import asyncio
import tempfile
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
        from bot.modules.archive_cache import ArchiveCache
        self.zip_directory = tempfile.TemporaryDirectory()
        self.zip_cache = ArchiveCache(self.zip_directory.name, 10 * 1024 * 1024)
        self.addCleanup(self.zip_directory.cleanup)
        self.addAsyncCleanup(self.zip_cache.close)
        self.cache_patch = patch.object(catalog, 'archive_cache', self.zip_cache)
        self.cache_patch.start()
        self.addCleanup(self.cache_patch.stop)
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

    async def test_bulk_download_manager_can_get_and_head_without_browser_cookies(self):
        import io
        from datetime import datetime, timedelta, timezone
        from zipfile import ZipFile
        from quart import Response
        from werkzeug.datastructures import MultiDict
        from bot.server import main
        from urllib.parse import urlencode
        tickets = {}
        async def insert(document): tickets[document['_id']] = document
        async def find(query): return tickets.get(query['_id'])
        storage = SimpleNamespace(insert_one=insert, find_one=find)
        files = [dict(code='episode1', file_name='../same.mp4', file_size=4),
                 dict(code='episode2', file_name='বাংলা.mp4', file_size=4)]
        async def download(code, range_allowed=True, parallel=False, internal=False):
            self.assertFalse(range_allowed)
            self.assertFalse(parallel)
            self.assertTrue(internal)
            async def body():
                yield b'ab'
                yield b'cd'
            return Response(body(), headers={'Content-Length': '4'})
        data = dict(files=[], total=0, page=1, pages=1)
        with patch.object(catalog, 'list_files', AsyncMock(return_value=data)):
            response = await self.client.get('/index', scheme='https')
        csrf = re.search(r'name="csrf" value="([^"]+)"', await response.get_data(as_text=True)).group(1)
        form = MultiDict([('csrf', csrf), ('section', 'index'), ('mode', 'prepare'), ('code', 'episode1'), ('code', 'episode2')])
        async def post(json=False):
            return await self.client.post('/index/download', data=urlencode(list(form.items(multi=True))),
                headers={'Content-Type':'application/x-www-form-urlencoded', 'Accept':'application/json' if json else 'text/html'}, scheme='https')
        with patch.object(catalog.db, 'db', {'download_tickets': storage}), patch.object(catalog, 'selected_files', AsyncMock(return_value=files)) as selected:
            with patch.object(main, 'transmit_file', download):
                response = await post()
                self.assertEqual(response.status_code, 303)
                prepare = response.headers['Location']
                token = prepare.rsplit('/',1)[1]
                await asyncio.wait_for(self.zip_cache.jobs[token]['task'], 1)
                prepared = await self.client.get(prepare, scheme='https')
                self.assertEqual(prepared.status_code, 302)
                link = prepared.headers['Location']
                self.assertTrue(link.endswith('/GateStream-index.zip'))
                manager = instance.test_client()  # No login or CSRF cookies in the external manager.
                async with manager.request(link, method='HEAD', scheme='https') as head:
                    await head.send(b'')
                    await head.send_complete()
                self.assertEqual(head.status_code, 200)
                response = await manager.get(link, scheme='https')
                payload = await response.get_data()
                self.assertEqual(response.status_code, 200)
                self.assertEqual(int(head.headers['Content-Length']), len(payload))
                self.assertEqual(response.headers['Accept-Ranges'], 'bytes')
                etag = response.headers['ETag']
                # Pause after a prefix, then resume without regenerating any bytes.
                prefix = await manager.get(link, headers={'Range':'bytes=0-9'}, scheme='https')
                resumed = await manager.get(link, headers={'Range':'bytes=10-', 'If-Range':etag}, scheme='https')
                self.assertEqual(prefix.status_code, 206)
                self.assertEqual(resumed.status_code, 206)
                self.assertEqual(resumed.headers['Content-Range'], f'bytes 10-{len(payload)-1}/{len(payload)}')
                self.assertEqual(await prefix.get_data() + await resumed.get_data(), payload)
                parts = await asyncio.gather(*[manager.get(link, headers={'Range':f'bytes={i}-{min(i+36,len(payload)-1)}'}, scheme='https') for i in range(0,len(payload),37)])
                self.assertTrue(all(part.status_code == 206 for part in parts))
                self.assertEqual(b''.join([await part.get_data() for part in parts]),payload)
                suffix = await manager.get(link, headers={'Range':'bytes=-22'}, scheme='https')
                self.assertEqual(await suffix.get_data(),payload[-22:])
                invalid = await manager.get(link, headers={'Range':f'bytes={len(payload)}-'}, scheme='https')
                self.assertEqual(invalid.status_code,416)
                self.assertEqual(invalid.headers['Content-Range'],f'bytes */{len(payload)}')
                stale = await manager.get(link, headers={'Range':'bytes=10-', 'If-Range':'"stale"'}, scheme='https')
                self.assertEqual(stale.status_code,200)
                self.assertEqual(await stale.get_data(),payload)
                with ZipFile(io.BytesIO(payload)) as archive:
                    self.assertEqual(archive.namelist(), ['01-.._same.mp4', '02-বাংলা.mp4'])
                    self.assertEqual([archive.read(n) for n in archive.namelist()], [b'abcd', b'abcd'])
                response = await post(json=True)
                self.assertEqual(response.status_code, 200)
                self.assertTrue((await response.get_json())['url'].endswith('.zip'))
                self.assertIn('/status',(await response.get_json())['status_url'])
                form['section'] = 'link'
                selected.reset_mock()
                self.assertEqual((await post()).status_code, 401)
                selected.assert_not_awaited()
                await self.login()
                response = await post()
                private_token = response.headers['Location'].rsplit('/',1)[1]
                await asyncio.wait_for(self.zip_cache.jobs[private_token]['task'],1)
                private_link = f'/index/archive/{private_token}/GateStream-link.zip'
                self.assertEqual((await manager.get(private_link, scheme='https')).status_code, 200)
                self.settings.return_value = index_access._record('New password')
                self.assertEqual((await manager.get(private_link, scheme='https')).status_code, 410)
                self.assertEqual((await manager.get(f'/index/archive/{private_token}/status', scheme='https')).status_code, 410)
                public_ticket = next(t for t in tickets.values() if t['section']=='index')
                public_ticket['expires_at'] = datetime.now(timezone.utc)-timedelta(seconds=1)
                self.assertEqual((await manager.get(link, scheme='https')).status_code, 410)
            form['section'] = 'index'
            selected.return_value = []
            self.assertEqual((await post()).status_code, 409)
            form.setlist('code', [str(i) for i in range(31)])
            self.assertEqual((await post()).status_code, 400)
            form.setlist('code', ['../../bad'])
            self.assertEqual((await post()).status_code, 400)
            form.setlist('code', ['episode1'])
            form['csrf'] = 'wrong'
            self.assertEqual((await post()).status_code, 400)

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
        self.assertIn('Start immediately', body)
        self.assertIn('Prepare with resume', body)
        self.assertIn('Resume and multiple connections', body)
        self.assertIn('Season 1', body)
        self.assertIn('&lt;script&gt;', body)
        self.assertNotIn('<script>alert(1)</script>', body)
        self.assertIn('Page 2 of 3', body)
        self.assertIn('page=3', body)
        csrf = re.search(r'name="csrf" value="([^"]+)"', body).group(1)
        self.assertEqual((await self.client.post('/index/logout', form={'csrf': csrf}, scheme='https')).status_code, 302)
        body = await (await self.client.get('/index?section=link', scheme='https')).get_data(as_text=True)
        self.assertIn('Unlock Link', body)

    async def test_immediate_mode_shares_one_build_supports_ranges_and_enforces_access(self):
        import io
        from zipfile import ZipFile
        from quart import Response
        from bot.server import main
        tickets = {}
        async def insert(document): tickets[document['_id']] = document
        storage = SimpleNamespace(insert_one=insert,find_one=AsyncMock(side_effect=lambda q:tickets.get(q['_id'])))
        files = [dict(code='one',file_name='one.bin',file_size=4)]
        form = dict(csrf=await self.csrf(),section='index',mode='stream',code='one')
        async def download(code,range_allowed=True,internal=False):
            self.assertFalse(range_allowed)
            self.assertTrue(internal)
            async def body():
                yield b'ab'
                yield b'cd'
            return Response(body(),headers={'Content-Length':'4'})
        manager = instance.test_client()
        with patch.object(catalog.db,'db',{'download_tickets':storage}), \
             patch.object(catalog,'selected_files',AsyncMock(return_value=files)), \
             patch.object(main,'transmit_file',AsyncMock(side_effect=download)) as source:
            response = await self.client.post('/index/download',form=form,scheme='https')
            self.assertEqual(response.status_code,303)
            link = response.headers['Location']
            self.assertTrue(link.endswith('/GateStream-index.zip'))
            token = link.split('/')[-2]
            status = await manager.get(f'/index/archive/{token}/status',scheme='https')
            self.assertIn(status.status_code,(200,202))
            async with manager.request(link,method='HEAD',scheme='https') as head:
                await head.send(b'')
                await head.send_complete()
            self.assertEqual(head.status_code,200)
            self.assertEqual(head.headers['Accept-Ranges'],'bytes')
            response = await manager.get(link,scheme='https')
            self.assertEqual(response.status_code,200)
            payload = await response.get_data()
            partial = await manager.get(link,headers={'Range':'bytes=0-9'},scheme='https')
            self.assertEqual(partial.status_code,206)
            self.assertEqual(await partial.get_data(),payload[:10])
            self.assertEqual(source.await_count,1)
            self.assertEqual(len(payload),int(head.headers['Content-Length']))
            with ZipFile(io.BytesIO(payload)) as archive:
                self.assertIsNone(archive.testzip())
                self.assertEqual(archive.read('01-one.bin'),b'abcd')
            response = await self.client.post('/index/download',form=form,headers={'Accept':'application/json'},scheme='https')
            self.assertEqual((await response.get_json())['mode'],'stream')
            form['mode'] = 'invalid'
            self.assertEqual((await self.client.post('/index/download',form=form,scheme='https')).status_code,400)
            form.update(mode='stream',section='link')
            self.assertEqual((await self.client.post('/index/download',form=form,scheme='https')).status_code,401)
            await self.login()
            response = await self.client.post('/index/download',form=form,scheme='https')
            private_link = response.headers['Location']
            self.assertEqual((await manager.get(private_link,scheme='https')).status_code,200)
            self.settings.return_value = index_access._record('Changed password')
            self.assertEqual((await manager.get(private_link,scheme='https')).status_code,410)
            self.assertTrue(all(job['progressive'] for job in self.zip_cache.jobs.values()))

    async def test_preparation_progress_restart_and_missing_cache_fail_closed(self):
        from datetime import datetime, timedelta, timezone
        token = 'a' * 43
        files = [dict(code='one', file_name='file.bin', file_size=4)]
        ticket = dict(_id=token, section='index', codes=['one'], files=files,
            archive_version=2, expires_at=datetime.now(timezone.utc)+timedelta(hours=24))
        storage = SimpleNamespace(find_one=AsyncMock(return_value=ticket))
        release = asyncio.Event()
        async def download(code):
            await release.wait()
            from quart import Response
            return Response(b'abcd', headers={'Content-Length':'4'})
        await self.zip_cache.start(token, files, download, ticket['expires_at'].timestamp())
        link = f'/index/archive/{token}/GateStream-index.zip'
        with patch.object(catalog.db,'db',{'download_tickets':storage}), \
             patch.object(catalog,'selected_files',AsyncMock(return_value=files)):
            response = await self.client.get(f'/index/prepare/{token}',scheme='https')
            self.assertEqual(response.status_code,202)
            self.assertIn('Preparing your ZIP',await response.get_data(as_text=True))
            status = await self.client.get(f'/index/archive/{token}/status',scheme='https')
            self.assertEqual(status.status_code,202)
            self.assertIn((await status.get_json())['state'],('queued','preparing'))
            waiting = await self.client.get(link,scheme='https')
            self.assertEqual(waiting.status_code,503)
            self.assertEqual(waiting.headers['Retry-After'],'2')
            release.set()
            await asyncio.wait_for(self.zip_cache.jobs[token]['task'],1)
            full = await self.client.get(link,scheme='https')
            payload = await full.get_data()
            from bot.modules.archive_cache import ArchiveCache
            restarted = ArchiveCache(self.zip_directory.name, self.zip_cache.max_bytes)
            with patch.object(catalog,'archive_cache',restarted):
                resumed = await self.client.get(link,headers={'Range':'bytes=13-','If-Range':full.headers['ETag']},scheme='https')
                self.assertEqual(resumed.status_code,206)
                self.assertEqual(await resumed.get_data(),payload[13:])
                self.assertEqual(resumed.headers['ETag'],full.headers['ETag'])
                restarted.path(token).unlink()
                self.assertEqual((await self.client.get(link,scheme='https')).status_code,410)
            ticket['archive_version'] = 0
            self.assertEqual((await self.client.get(link,scheme='https')).status_code,410)

    async def test_live_http_ranges_resume_before_and_after_archive_completion(self):
        from datetime import datetime, timedelta, timezone
        from quart import Response
        from bot.modules.archive import archive_size
        from bot.modules.archive_cache import ArchiveCache
        from zipfile import ZipFile
        import io
        token='c'*43
        files=[dict(code='one',file_name='one.bin',file_size=4)]
        ticket=dict(_id=token,section='index',codes=['one'],files=files,mode='stream',archive_version=3,
            expires_at=datetime.now(timezone.utc)+timedelta(hours=24))
        entered,release=asyncio.Event(),asyncio.Event()
        calls=[]
        async def download(code):
            calls.append(code)
            async def body():
                yield b'ab'
                entered.set()
                await release.wait()
                yield b'cd'
            return Response(body(),headers={'Content-Length':'4'})
        await self.zip_cache.start(token,files,download,ticket['expires_at'].timestamp(),progressive=True)
        await asyncio.wait_for(entered.wait(),1)
        link=f'/index/archive/{token}/GateStream-index.zip'
        with patch.object(catalog.db,'db',{'download_tickets':SimpleNamespace(find_one=AsyncMock(return_value=ticket))}), \
             patch.object(catalog,'selected_files',AsyncMock(return_value=files)):
            async with self.client.request(link,method='HEAD',scheme='https') as head:
                await head.send(b'')
                await head.send_complete()
            etag=head.headers['ETag']
            self.assertEqual(head.headers['Accept-Ranges'],'bytes')
            self.assertEqual(int(head.headers['Content-Length']),archive_size(files))
            prefix=await asyncio.wait_for(self.client.get(link,headers={'Range':'bytes=0-1'},scheme='https'),1)
            self.assertEqual(prefix.status_code,206)
            self.assertEqual(await prefix.get_data(),b'PK')
            self.assertFalse(self.zip_cache.jobs[token]['task'].done())
            suffix=asyncio.create_task(self.client.get(link,headers={'Range':'bytes=-22','If-Range':etag},scheme='https'))
            resumed=asyncio.create_task(self.client.get(link,headers={'Range':'bytes=2-','If-Range':etag},scheme='https'))
            await asyncio.sleep(.01)
            self.assertFalse(suffix.done())
            release.set()
            tail,rest=await asyncio.wait_for(asyncio.gather(suffix,resumed),1)
            self.assertEqual(tail.status_code,206)
            self.assertEqual(rest.status_code,206)
            payload=b'PK'+await rest.get_data()
            self.assertEqual(await tail.get_data(),payload[-22:])
            self.assertEqual(rest.headers['ETag'],etag)
            with ZipFile(io.BytesIO(payload)) as archive:
                self.assertIsNone(archive.testzip())
                self.assertEqual(archive.read('01-one.bin'),b'abcd')
            await self.zip_cache.jobs[token]['task']
            restarted=ArchiveCache(self.zip_directory.name,self.zip_cache.max_bytes)
            with patch.object(catalog,'archive_cache',restarted):
                response=await self.client.get(link,headers={'Range':'bytes=17-','If-Range':etag},scheme='https')
                self.assertEqual(response.status_code,206)
                self.assertEqual(response.headers['ETag'],etag)
                self.assertEqual(await response.get_data(),payload[17:])
            self.assertEqual(calls,['one'])

    async def test_large_archive_ranges_seek_past_two_gibibytes(self):
        from datetime import datetime, timedelta, timezone
        from bot.modules.archive import archive_size
        token = 'b' * 43
        files = [dict(code='large',file_name='large.bin',file_size=3*1024**3)]
        total = archive_size(files)
        offset = 2*1024**3+123
        path = self.zip_cache.path(token)
        # A sparse test file exercises 64-bit seeks without allocating 3 GiB.
        with path.open('wb') as output:
            output.truncate(total)
            output.seek(offset)
            output.write(b'ZIP')
        ticket = dict(_id=token,section='index',codes=['large'],files=files,archive_version=2,
            expires_at=datetime.now(timezone.utc)+timedelta(hours=24))
        with patch.object(catalog.db,'db',{'download_tickets':SimpleNamespace(find_one=AsyncMock(return_value=ticket))}), \
             patch.object(catalog,'selected_files',AsyncMock(return_value=files)):
            response = await self.client.get(f'/index/archive/{token}/GateStream-index.zip',
                headers={'Range':f'bytes={offset}-{offset+2}'},scheme='https')
            self.assertEqual(response.status_code,206)
            self.assertEqual(response.content_length,3)
            self.assertEqual(response.headers['Content-Range'],f'bytes {offset}-{offset+2}/{total}')
            self.assertEqual(await response.get_data(),b'ZIP')

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
