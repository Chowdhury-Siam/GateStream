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

    async def test_unknown_file_is_404(self):
        with patch.object(main, 'get_file', AsyncMock(return_value=None)):
            response = await instance.test_client().get('/dl/missing-code')
        self.assertEqual(response.status_code, 404)


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
                    with patch.object(self.files, 'get_file_properties', return_value=properties):
                        with patch.object(self.files, 'save_file', AsyncMock(return_value={'code': 'test-code'})):
                            await self.files.handle_user_file.__wrapped__(None, message)
                caption = status.edit_text.call_args.kwargs['text']
                self.assertIn(properties[-1], caption)
                self.assertIn('https://user-app.hf.space/dl/test-code', caption)


class DriveCommandTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        with patch.object(clients.TelegramBot, 'add_handler'):
            from bot.plugins import drive
        cls.drive = drive

    def message(self, user_id=12345):
        from hydrogram.types import Message
        from unittest.mock import MagicMock
        message = MagicMock(spec=Message)
        message.from_user = SimpleNamespace(id=user_id)
        message.text = '/drive https://example.com/file.bin'
        message.reply = AsyncMock(return_value=SimpleNamespace(edit_text=AsyncMock()))
        return message

    async def test_only_admins_can_start_transfers_and_feature_defaults_off(self):
        message = self.message(user_id=999)
        with patch.object(self.drive.Drive, 'ENABLED', True), patch.object(self.drive, 'load_credentials') as load:
            await self.drive.drive_command(None, message)
            load.assert_not_called()
            self.assertIn('Administrators', message.reply.call_args.kwargs['text'])
        message = self.message()
        with patch.object(self.drive.Drive, 'ENABLED', False), patch.object(self.drive, 'load_credentials') as load:
            await self.drive.drive_command(None, message)
            load.assert_not_called()
            self.assertIn('disabled', message.reply.call_args.args[0])

    async def test_failed_download_cleans_temporary_file(self):
        from pathlib import Path
        paths = []

        async def fail_download(url, directory, limit, progress=None):
            path = Path(directory) / 'payload'
            path.write_bytes(b'partial-download')
            paths.append(path)
            raise self.drive.DriveTransferError('The file exceeds DRIVE_MAX_MB.')

        message = self.message()
        with patch.object(self.drive.Drive, 'ENABLED', True), patch.object(self.drive, 'load_credentials', return_value=object()):
            with patch.object(self.drive, 'download_file', fail_download), patch.object(self.drive, 'upload_file') as upload:
                await self.drive.drive_command(None, message)
                upload.assert_not_called()
        self.assertTrue(paths)
        self.assertFalse(paths[0].exists())
        self.assertFalse(self.drive._job_lock.locked())

    async def test_success_cleans_download_and_returns_drive_button(self):
        from pathlib import Path
        paths = []

        async def download(url, directory, limit, progress=None):
            path = Path(directory) / 'payload'
            path.write_bytes(b'abc')
            paths.append(path)
            return path, 'file.bin'

        message = self.message()
        message.caption = message.text
        message.text = None
        with patch.object(self.drive.Drive, 'ENABLED', True), patch.object(self.drive, 'load_credentials', return_value=object()):
            with patch.object(self.drive, 'download_file', download), patch.object(self.drive, 'upload_file', return_value={'id': 'new-file'}):
                await self.drive.drive_command(None, message)
        self.assertFalse(paths[0].exists())
        self.assertEqual(message.reply.return_value.edit_text.call_args.kwargs['reply_markup'].inline_keyboard[0][0].url,
                         'https://drive.google.com/file/d/new-file/view')


class ActivityEndpointTests(unittest.IsolatedAsyncioTestCase):
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


if __name__ == '__main__':
    unittest.main()
