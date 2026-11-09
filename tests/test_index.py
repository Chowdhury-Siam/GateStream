import os
import unittest
import asyncio
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

with patch.dict(os.environ, {
    'TELEGRAM_API_ID': '12345', 'TELEGRAM_API_HASH': 'test_hash',
    'TELEGRAM_BOT_TOKEN': '12345:test_token', 'TELEGRAM_BOT_USERNAME': 'TestGateStreamBot',
    'OWNER_ID': '12345', 'DATABASE_URL': 'mongodb://localhost:27017',
    'SPACE_ID': 'user/app', 'SPACE_HOST': 'user-app.hf.space',
}, clear=True):
    from bot.server import instance, catalog
    from bot.modules import personal_library as library
    from test_personal_library import csrf


class ArchiveRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from bot.modules.archive_cache import ArchiveCache
        self.zip_directory = tempfile.TemporaryDirectory()
        self.zip_cache = ArchiveCache(self.zip_directory.name, 10 * 1024 * 1024)
        self.addCleanup(self.zip_directory.cleanup)
        self.addAsyncCleanup(self.zip_cache.close)
        self.cache_patch = patch.object(catalog, 'archive_cache', self.zip_cache)
        self.cache_patch.start()
        self.addCleanup(self.cache_patch.stop)
        self.client = instance.test_client()

    async def test_retired_catalog_routes_and_tickets_are_unavailable(self):
        from datetime import datetime, timedelta, timezone

        for path in ('/channel-catalog', '/channel-catalog?section=link', '/api/movies'):
            self.assertEqual((await self.client.get(path)).status_code, 404)
        for path in ('/index/login', '/index/logout', '/index/download'):
            self.assertEqual((await self.client.post(path)).status_code, 404)
        library_page = await self.client.get('/index')
        self.assertEqual(library_page.status_code, 200)
        self.assertNotIn('/channel-catalog', await library_page.get_data(as_text=True))

        token = 'a' * 43
        for section in ('index', 'link'):
            ticket = dict(section=section, archive_version=3, expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
            storage = SimpleNamespace(find_one=AsyncMock(return_value=ticket))
            with patch.object(catalog.db, 'db', {'download_tickets': storage}):
                for path in (f'/index/archive/{token}/status', f'/index/archive/{token}/Old.zip', f'/index/prepare/{token}'):
                    self.assertEqual((await self.client.get(path)).status_code, 410)

    async def test_preparation_progress_restart_and_missing_cache_fail_closed(self):
        from datetime import datetime, timedelta, timezone
        token = 'a' * 43
        files = [dict(code='one', file_name='file.bin', file_size=4)]
        ticket = dict(_id=token, section='personal', user_id=1, folder_id='d'*43, files=files, filename='My prepared ZIP.zip', mode='prepare',
            archive_version=3, expires_at=datetime.now(timezone.utc)+timedelta(hours=24))
        storage = SimpleNamespace(find_one=AsyncMock(return_value=ticket))
        release = asyncio.Event()
        async def download(code):
            await release.wait()
            from quart import Response
            return Response(b'abcd', headers={'Content-Length':'4'})
        await self.zip_cache.start(token, files, download, ticket['expires_at'].timestamp())
        link = f'/index/archive/{token}/GateStream-index.zip'
        with patch.object(catalog.db,'db',{'download_tickets':storage}), \
             patch.object(library,'folder_files',AsyncMock(return_value=files)):
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
            self.assertIn('filename="My prepared ZIP.zip"',full.headers['Content-Disposition'])
            ready = await self.client.get(f'/index/prepare/{token}',scheme='https')
            from urllib.parse import unquote
            self.assertTrue(unquote(ready.headers['Location']).endswith('/My prepared ZIP.zip'))
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

    async def test_shared_folder_returns_live_ticket_even_from_an_old_prepare_form(self):
        from quart import Response
        from bot.server import main
        entered, release = asyncio.Event(), asyncio.Event()
        tickets = {}
        async def insert(ticket): tickets[ticket['_id']] = ticket
        storage = SimpleNamespace(insert_one=insert, find_one=AsyncMock(side_effect=lambda query: tickets.get(query['_id'])))
        files = [dict(code='one', file_name='episode.mkv', file_size=65536)]
        async def download(*args, **kwargs):
            async def body():
                yield b'a' * 32768
                entered.set()
                await release.wait()
                yield b'b' * 32768
            return Response(body(), headers={'Content-Length': '65536'})
        folder_id = 'd' * 43
        path = f'/index/share/{folder_id}'
        folder = {'_id': folder_id, 'name': 'My folder', 'user_id': 1}
        with patch.object(catalog.db, 'db', {'download_tickets': storage}), \
             patch.object(library, 'folder_by_id', AsyncMock(return_value=folder)), \
             patch.object(library, 'folder_count', AsyncMock(return_value=1)), \
             patch.object(library, 'folder_files', AsyncMock(return_value=files)), \
             patch.object(main, 'transmit_file', download):
            landing = await self.client.get(path, scheme='https')
            response = await asyncio.wait_for(self.client.post(path,
                form={'csrf': await csrf(landing), 'mode': 'prepare'}, scheme='https'), 1)
            self.assertEqual(response.status_code, 303)
            await asyncio.wait_for(entered.wait(), 1)
            token = next(iter(tickets))
            self.assertEqual(tickets[token]['mode'], 'stream')
            self.assertIn(f'/index/archive/{token}/', response.headers['Location'])
            status_url = f'/index/archive/{token}/status'
            status = await self.client.get(status_url, scheme='https')
            progress = await status.get_json()
            self.assertEqual(status.status_code, 202)
            self.assertEqual(progress['state'], 'preparing')
            self.assertGreaterEqual(progress['bytes'], 32768)
            self.assertLess(progress['bytes'], progress['total'])
            self.assertGreater(progress['source_speed_bps'], 0)
            release.set()
            await asyncio.wait_for(self.zip_cache.jobs[token]['task'], 1)
            ready = await (await self.client.get(status_url, scheme='https')).get_json()
            self.assertEqual(ready['state'], 'ready')
            self.assertEqual(ready['bytes'], ready['total'])

    async def test_zip_streams_without_disk_when_it_cannot_fit_in_cache(self):
        import io
        from zipfile import ZipFile
        from quart import Response
        from bot.server import main

        self.zip_cache.max_bytes = 1024
        payload = b'x' * 2048
        files = [dict(code='one', file_name='file.bin', file_size=len(payload))]
        tickets = {}

        async def insert(ticket):
            tickets[ticket['_id']] = ticket

        async def update(query, changes):
            tickets[query['_id']].update(changes['$set'])

        storage = SimpleNamespace(insert_one=insert, update_one=update,
            find_one=AsyncMock(side_effect=lambda q: tickets.get(q['_id'])))
        source = AsyncMock(return_value=Response(payload))
        with patch.object(catalog.db, 'db', {'download_tickets': storage}), \
             patch.object(library, 'folder_files', AsyncMock(return_value=files)), \
             patch.object(main, 'transmit_file', source):
            token = await catalog.create_archive(files, 'Oversize.zip', folder_id='d' * 43, user_id=1)
            self.assertEqual(tickets[token]['mode'], 'direct')
            path = f'/index/archive/{token}/Oversize.zip'
            async with self.client.request(path, method='HEAD') as head:
                await head.send(b'')
                await head.send_complete()
            self.assertEqual(head.headers['Accept-Ranges'], 'none')
            source.assert_not_awaited()

            response = await self.client.get(path, headers={'Range': 'bytes=2-'})
            self.assertEqual(response.status_code, 200)
            archive = await response.get_data()
            self.assertEqual(int(head.headers['Content-Length']), len(archive))
            with ZipFile(io.BytesIO(archive)) as z:
                self.assertEqual(z.read('01-file.bin'), payload)
                self.assertIsNone(z.testzip())
            source.assert_awaited_once_with('one', range_allowed=False, internal=True, parallel=True)
            self.assertFalse(self.zip_cache.jobs)
            self.assertEqual(os.listdir(self.zip_directory.name), [])

    async def test_live_http_ranges_resume_before_and_after_archive_completion(self):
        from datetime import datetime, timedelta, timezone
        from quart import Response
        from bot.modules.archive import archive_size
        from bot.modules.archive_cache import ArchiveCache
        from zipfile import ZipFile
        import io
        token='c'*43
        files=[dict(code='one',file_name='one.bin',file_size=4)]
        ticket=dict(_id=token,section='personal',user_id=1,folder_id='d'*43,filename='GateStream-index.zip',files=files,mode='stream',archive_version=3,
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
             patch.object(library,'folder_files',AsyncMock(return_value=files)):
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
        ticket = dict(_id=token,section='personal',user_id=1,folder_id='d'*43,filename='GateStream-index.zip',files=files,archive_version=3,
            expires_at=datetime.now(timezone.utc)+timedelta(hours=24))
        with patch.object(catalog.db,'db',{'download_tickets':SimpleNamespace(find_one=AsyncMock(return_value=ticket))}), \
             patch.object(library,'folder_files',AsyncMock(return_value=files)):
            response = await self.client.get(f'/index/archive/{token}/GateStream-index.zip',
                headers={'Range':f'bytes={offset}-{offset+2}'},scheme='https')
            self.assertEqual(response.status_code,206)
            self.assertEqual(response.content_length,3)
            self.assertEqual(response.headers['Content-Range'],f'bytes {offset}-{offset+2}/{total}')
            self.assertEqual(await response.get_data(),b'ZIP')
