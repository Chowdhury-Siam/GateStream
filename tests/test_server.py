import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch


with patch.dict(os.environ, {
    'TELEGRAM_API_ID': '12345',
    'TELEGRAM_API_HASH': 'test_hash',
    'TELEGRAM_BOT_TOKEN': '12345:test_token',
    'TELEGRAM_BOT_USERNAME': 'TestGateStreamBot',
    'OWNER_ID': '12345',
    'DATABASE_URL': 'mongodb://localhost:27017',
    'SPACE_ID': 'user/app',
    'SPACE_HOST': 'user-app.hf.space',
}, clear=True):
    from bot.server import instance, main
    from bot import clients


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_database_failure_stops_startup_before_telegram(self):
        database = SimpleNamespace(command=AsyncMock(side_effect=RuntimeError('database unreachable')))
        with patch.object(clients.db, 'connect'), patch.object(clients.db, 'db', database):
            with patch.object(clients.TelegramBot, 'start', AsyncMock()) as start:
                with self.assertRaisesRegex(RuntimeError, 'database unreachable'):
                    await clients.start_all_clients()
                start.assert_not_awaited()

    async def test_home_is_embeddable_page(self):
        response = await instance.test_client().get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('https://t.me/TestGateStreamBot', await response.get_data(as_text=True))
        self.assertNotIn('Location', response.headers)

    async def test_health_endpoints(self):
        for path in ('/health', '/ping'):
            response = await instance.test_client().get(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(await response.get_data(as_text=True), 'OK')

    async def test_player_uses_space_host(self):
        doc = {'file_name': 'Test video.mp4', 'file_size': 1024}
        with patch.object(main, 'get_file', AsyncMock(return_value=doc)):
            response = await instance.test_client().get('/stream/test-code')
        self.assertEqual(response.status_code, 200)
        self.assertIn('https://user-app.hf.space/dl/test-code', await response.get_data(as_text=True))

    async def test_movie_batch_episode_resolves_by_its_download_code(self):
        from bot.database import files, db
        batch = dict(_id='batch', channel_id=1, category='movies', episodes=[dict(
            code='episode', message_id=2, file_name='Movie.mp4', file_size=4, mime_type='video/mp4')])
        collection = SimpleNamespace(find_one=AsyncMock(side_effect=[None,batch]))
        with patch.multiple(db,movies=collection,anime=None,webseries=None,direct_files=None):
            document = await files.get_file('episode')
        self.assertEqual(document['code'],'episode')
        self.assertEqual(document['category'],'movies')
        self.assertEqual(document['channel_id'],1)

    async def test_unknown_file_is_404(self):
        with patch.object(main, 'get_file', AsyncMock(return_value=None)):
            response = await instance.test_client().get('/dl/missing-code')
        self.assertEqual(response.status_code, 404)

    async def test_download_uses_the_files_mime_type_for_full_and_range_requests(self):
        apk_type = 'application/vnd.android.package-archive'
        for name, stored_type, expected_type in (
            ('App.apk', 'application/octet-stream', apk_type),
            ('App.APK', 'video/mp4', apk_type),
            ('App.apk', None, apk_type),
            ('Archive.zip', 'application/octet-stream', 'application/zip'),
            ('Page.html', 'application/octet-stream', 'text/html'),
            ('Unknown.gatestreamtest', 'application/octet-stream', 'application/octet-stream'),
            ('Movie.mp4', 'application/octet-stream', 'video/mp4'),
            ('Movie.mkv', 'application/octet-stream', 'video/x-matroska'),
        ):
            with self.subTest(name=name, stored_type=stored_type):
                doc = dict(file_name=name, file_size=6, mime_type=stored_type, channel_id=1, message_id=2)
                message = SimpleNamespace(caption=None, document=SimpleNamespace(
                    file_name=name, file_size=6, mime_type='application/octet-stream'))
                with patch.multiple(main, get_file=AsyncMock(return_value=doc),
                                    get_message=AsyncMock(return_value=message),
                                    get_cached_chunk=AsyncMock(return_value=b'PK1234'),
                                    is_chunk_cached=lambda *_: True, add_bandwidth_bytes=AsyncMock(),
                                    flush_ram=lambda: None, check_memory_circuit_breaker=lambda: None):
                    client = instance.test_client()
                    head = await client.head('/dl/file')
                    full = await client.get('/dl/file')
                    partial = await client.get('/dl/file', headers={'Range': 'bytes=1-3'})
                    playback = await client.head('/dl/file?stream=1')
                for response, status in ((head, 200), (full, 200), (partial, 206)):
                    self.assertEqual(response.status_code, status)
                    self.assertEqual(response.headers['Content-Type'], expected_type)
                    self.assertEqual(response.headers['Content-Disposition'], f'attachment; filename="{name}"')
                disposition = 'inline' if expected_type.startswith('video/') else 'attachment'
                self.assertEqual(playback.headers['Content-Disposition'], f'{disposition}; filename="{name}"')
                self.assertEqual(await full.get_data(), b'PK1234')
                self.assertEqual(await partial.get_data(), b'K12')

    def test_apk_keeps_its_telegram_filename_when_it_has_a_caption(self):
        message = SimpleNamespace(caption='Download the latest build', document=SimpleNamespace(
            file_name='App.apk', file_size=6, mime_type='application/octet-stream'))
        name, _, mime_type, _, _ = main.get_file_properties(message)
        self.assertEqual(name, 'App.apk')
        self.assertEqual(mime_type, 'application/vnd.android.package-archive')


class TelegramReplyTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        with patch.object(clients.TelegramBot, 'add_handler'):
            from bot.plugins import files
        cls.files = files

    async def test_upload_help_preserves_placeholders_and_valid_entities(self):
        message = SimpleNamespace(forward_from_chat=None, reply=AsyncMock())
        with patch.object(self.files.Telegram, 'CHANNEL_ID', 0):
            await self.files.handle_user_file.__wrapped__(None, message)
        text = message.reply.call_args.args[0]
        parsed = await clients.TelegramBot.parser.parse(text)
        self.assertIn('/link <channel_message_link>', parsed['message'])
        self.assertIn('/batch <category> <start_link> <end_link>', parsed['message'])
        length = len(parsed['message'].rstrip().encode('utf-16-le')) // 2
        for entity in parsed['entities'] or []:
            self.assertGreater(entity.length, 0)
            self.assertLessEqual(entity.offset + entity.length, length)

    async def test_forwarded_channel_help_keeps_message_link(self):
        message = SimpleNamespace(
            forward_from_chat=SimpleNamespace(id=-1001234567890),
            forward_from_message_id=42,
            reply=AsyncMock(),
        )
        with patch.object(self.files.Telegram, 'CHANNEL_ID', 0):
            await self.files.handle_user_file.__wrapped__(None, message)
        parsed = await clients.TelegramBot.parser.parse(message.reply.call_args.args[0])
        self.assertIn('/link https://t.me/c/1234567890/42', parsed['message'])

    async def test_direct_upload_captions_include_duration(self):
        for video in (True, False):
            with self.subTest(video=video):
                status = SimpleNamespace(edit_text=AsyncMock())
                message = SimpleNamespace(
                    reply=AsyncMock(return_value=status),
                    copy=AsyncMock(return_value=SimpleNamespace(id=42)),
                    from_user=SimpleNamespace(id=12345),
                    document=None if video else SimpleNamespace(mime_type='application/pdf'),
                    video=video,
                )
                properties = ('Movie.mp4' if video else 'Book.pdf', 1024, 'video/mp4' if video else 'application/pdf', 65 if video else 0, '01:05' if video else 'N/A')
                with patch.object(self.files.Telegram, 'CHANNEL_ID', -1001234567890):
                    with patch.object(self.files, 'get_file_properties', return_value=properties), \
                         patch.object(self.files.library, 'active_folder', AsyncMock(return_value=None)):
                        with patch.object(self.files, 'save_file', AsyncMock(return_value={'code': 'test-code'})):
                            await self.files.handle_user_file.__wrapped__(None, message)
                caption = status.edit_text.call_args.kwargs['text']
                self.assertIn(properties[-1], caption)
                self.assertIn('https://user-app.hf.space/dl/test-code', caption)

    async def test_private_credentials_are_not_indexed_as_media(self):
        message = SimpleNamespace(document=SimpleNamespace(file_name='token.json'),reply=AsyncMock(),copy=AsyncMock())
        with patch.object(self.files,'save_file',AsyncMock()) as save:
            await self.files.handle_user_file.__wrapped__(None,message)
        save.assert_not_awaited()
        message.copy.assert_not_awaited()
        self.assertFalse(message.reply.call_args.kwargs['quote'])


class RetiredPluginTests(unittest.TestCase):
    def test_overlay_upgrade_cannot_load_retired_plugins(self):
        import importlib
        import tempfile
        from pathlib import Path
        imported=[]
        real_import=importlib.import_module
        def checked(name):
            imported.append(name)
            if name in ('bot.plugins.drive','bot.plugins.settings','bot.plugins.index_password'):
                raise AssertionError('Retired plugin was loaded')
            return real_import(name)
        previous=os.getcwd()
        with tempfile.TemporaryDirectory() as directory:
            tree=Path(directory)/'bot/plugins';tree.mkdir(parents=True)
            for name in ('drive','settings','index_password'):
                (tree/(name+'.py')).write_text('raise RuntimeError("retired code must not run")')
            try:
                os.chdir(directory)
                with patch('hydrogram.client.import_module',side_effect=checked),patch.object(clients.TelegramBot,'add_handler'):
                    clients.TelegramBot.load_plugins()
            finally:
                os.chdir(previous)
        self.assertIn('bot.plugins.channel_index',imported)
        self.assertNotIn('bot.plugins.index_password',imported)
        self.assertNotIn('bot.plugins.drive',imported)
        self.assertNotIn('bot.plugins.settings',imported)


class ActivityEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_download_grouping_uses_stable_worker_key_or_cloudflare_visitor_address(self):
        key = '1a' * 32
        async with instance.test_request_context('/dl/file', headers={'X-GateStream-Client': key}):
            self.assertEqual(main.download_client(), bytes.fromhex(key))
        hashes = []
        for proxy in ('192.0.2.1', '192.0.2.2'):
            async with instance.test_request_context('/dl/file', headers={
                'CF-Connecting-IP': '198.51.100.1', 'User-Agent': 'DownloadManager',
            }, scope_base={'client': (proxy, 1234)}):
                hashes.append(main.download_client())
        self.assertEqual(hashes[0], hashes[1])
        async with instance.test_request_context('/dl/file', headers={
            'CF-Connecting-IP': '198.51.100.2', 'User-Agent': 'DownloadManager',
            'X-GateStream-Client': 'invalid-key',
        }):
            self.assertNotEqual(main.download_client(), hashes[0])

    async def test_live_endpoint_is_uncached_and_private(self):
        from bot.modules.activity import TransferActivity
        activity = TransferActivity()
        ident = activity.start('media', 100, 'private-code')
        activity.progress(ident, 50)
        with patch.object(main, 'activity', activity):
            response = await instance.test_client().get('/api/activity')
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response.headers['Cache-Control'])
        data = await response.get_json()
        self.assertEqual(data['active_media_links'], 1)
        self.assertEqual(data['phases']['media']['bytes'], 50)
        self.assertNotIn('private-code', await response.get_data(as_text=True))

    async def test_head_has_file_size_and_range_download_is_counted(self):
        from bot.modules.activity import TransferActivity
        activity = TransferActivity()
        doc = dict(file_name='file.mp4', file_size=6, mime_type='video/mp4', channel_id=1, message_id=2)
        with patch.object(main, 'activity', activity), patch.object(main, 'get_file', AsyncMock(return_value=doc)):
            with patch.object(main, 'get_cached_chunk', AsyncMock(return_value=b'abcdef')), patch.object(main, 'is_chunk_cached', return_value=True):
                with patch.object(main, 'add_bandwidth_bytes', AsyncMock()), patch.object(main, 'flush_ram'), patch.object(main, 'check_memory_circuit_breaker'):
                    # Inspect ASGI headers directly: Quart's buffered test response rewrites empty HEAD bodies to length zero.
                    async with instance.test_client().request('/dl/private-code', method='HEAD') as head:
                        await head.send(b'')
                        await head.send_complete()
                    self.assertEqual(head.headers['Content-Length'], '6')
                    self.assertEqual(activity.snapshot()['phases']['media']['bytes'], 0)
                    response = await instance.test_client().get('/dl/private-code', headers={'Range': 'bytes=1-3'})
                    self.assertEqual(response.status_code, 206)
                    self.assertEqual(await response.get_data(), b'bcd')
        phase = activity.snapshot()['phases']['media']
        self.assertEqual((phase['bytes'],phase['completed'],phase['active']), (3,1,0))




    async def test_single_file_total_combines_ranges_and_excludes_playback(self):
        from bot.modules.activity import TransferActivity
        tracker = TransferActivity()
        doc = dict(file_name='file.mp4',file_size=6,mime_type='video/mp4',channel_id=1,message_id=2)
        with patch.object(main,'activity',tracker), patch.object(main,'get_file',AsyncMock(return_value=doc)), \
             patch.object(main,'get_cached_chunk',AsyncMock(return_value=b'abcdef')), \
             patch.object(main,'is_chunk_cached',return_value=True),patch.object(main,'add_bandwidth_bytes',AsyncMock()), \
             patch.object(main,'flush_ram'),patch.object(main,'check_memory_circuit_breaker'):
            client=instance.test_client()
            await client.head('/dl/file')
            await client.get('/dl/file?stream=1')
            self.assertEqual(tracker.snapshot()['phases']['media']['single_downloads'],0)
            for value in ['bytes=0-2','bytes=0-0','bytes=3-999']:
                await client.get('/dl/file',headers={'Range':value})
            self.assertEqual(tracker.snapshot()['phases']['media']['single_downloads'],1)
            self.assertEqual(tracker.snapshot()['phases']['media']['zip_downloads'],0)
