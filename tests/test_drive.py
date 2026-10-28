import base64
import importlib.util
import pickle
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from google.oauth2.credentials import Credentials

# Load the I/O module without constructing Telegram clients or connecting to services.
SPEC = importlib.util.spec_from_file_location('drive_transfer', Path(__file__).resolve().parents[1] / 'bot/modules/drive.py')
drive = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(drive)


class Response:
    def __init__(self, chunks=(), size=None, status=200, headers=None, content_type='application/octet-stream', filename=None):
        self.status = status
        self.headers = headers or {}
        self.content_length = size
        self.content_type = content_type
        self.content_disposition = SimpleNamespace(filename=filename) if filename else None
        self.content = self
        self.chunks = chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def iter_chunked(self, size):
        for chunk in self.chunks:
            yield chunk


class DriveTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_urls_and_dns_rebinding_defense(self):
        for url in ['file:///etc/passwd', 'http://127.0.0.1/a', 'http://169.254.169.254/a',
                    'http://[::1]/', 'http://[::ffff:127.0.0.1]/', 'http://[2002:7f00:1::]/',
                    'https://user:pass@example.com/a', 'http://example.com:27017/a',
                    'http://224.0.0.1/', 'http://10.0.0.1/', 'https://example.com/\nsecret']:
            with self.subTest(url=url), self.assertRaises(drive.DriveTransferError):
                drive.validate_url(url)
        drive.validate_url('https://example.com/file?signature=a%2Fb')
        resolver = drive.PublicResolver()
        try:
            with patch.object(drive.aiohttp.resolver.ThreadedResolver, 'resolve', AsyncMock(return_value=[{'host': '127.0.0.1'}])):
                with self.assertRaises(drive.DriveTransferError):
                    await resolver.resolve('public-looking.example', 443)
            with patch.object(drive.aiohttp.resolver.ThreadedResolver, 'resolve', AsyncMock(return_value=[{'host': '8.8.8.8'}])):
                self.assertEqual((await resolver.resolve('example.com', 443))[0]['host'], '8.8.8.8')
        finally:
            await resolver.close()

    async def test_download_preserves_signed_url_and_sanitizes_remote_filename(self):
        session = MagicMock()
        session.get.return_value = Response([b'abc', b'def'], size=6, filename='../../video.mp4')
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=session)
        context.__aexit__ = AsyncMock(return_value=False)
        with tempfile.TemporaryDirectory() as directory, patch.object(drive.aiohttp, 'ClientSession', return_value=context):
            path, name = await drive.download_file('https://example.com/a?signature=a%2Fb', directory, 10)
            self.assertEqual(path.read_bytes(), b'abcdef')
            self.assertEqual(path.parent, Path(directory))
            self.assertNotIn('/', name)
            self.assertEqual(str(session.get.call_args.args[0]), 'https://example.com/a?signature=a%2Fb')
            self.assertFalse(session.get.call_args.kwargs['allow_redirects'])
        self.assertFalse(path.exists())

    async def test_redirects_cannot_reach_private_hosts(self):
        session = MagicMock()
        session.get.return_value = Response(status=302, headers={'Location': 'http://127.0.0.1/token'})
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=session)
        context.__aexit__ = AsyncMock(return_value=False)
        with tempfile.TemporaryDirectory() as directory, patch.object(drive.aiohttp, 'ClientSession', return_value=context):
            with self.assertRaises(drive.DriveTransferError):
                await drive.download_file('https://example.com/a', directory, 10)
        self.assertEqual(session.get.call_count, 1)

    async def test_size_and_disk_limits_and_html_are_rejected(self):
        cases = [Response(size=11), Response([b'123456', b'78901']),
                 Response(content_type='text/html'), Response([b'abc'], size=6)]
        for response in cases:
            session = MagicMock()
            session.get.return_value = response
            context = MagicMock()
            context.__aenter__ = AsyncMock(return_value=session)
            context.__aexit__ = AsyncMock(return_value=False)
            with self.subTest(response=response), tempfile.TemporaryDirectory() as directory:
                with patch.object(drive.aiohttp, 'ClientSession', return_value=context):
                    with self.assertRaises(drive.DriveTransferError):
                        await drive.download_file('https://example.com/a', directory, 10)
        with tempfile.TemporaryDirectory() as directory:
            session.get.return_value = Response([b'abc'], size=3)
            with patch.object(drive.aiohttp, 'ClientSession', return_value=context), patch.object(drive.shutil, 'disk_usage', return_value=SimpleNamespace(free=1)):
                with self.assertRaisesRegex(drive.DriveTransferError, 'disk'):
                    await drive.download_file('https://example.com/a', directory, 10)

    async def test_token_file_and_base64_secret(self):
        credentials = Credentials(token='dummy-test-token', refresh_token='dummy-refresh',
            token_uri='https://oauth2.googleapis.com/token', client_id='dummy-client', client_secret='dummy-secret')
        data = pickle.dumps(credentials)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'token.pickle'
            path.write_bytes(data)
            self.assertEqual(drive.load_credentials(path).token, 'dummy-test-token')
            self.assertEqual(drive.load_credentials('missing', base64.b64encode(data).decode()).token, 'dummy-test-token')
        for secret in ['not base64!', base64.b64encode(pickle.dumps({'wrong': 'format'})).decode()]:
            with self.assertRaises(drive.DriveTransferError):
                drive.load_credentials('missing', secret)

    async def test_resumable_upload_uses_folder_and_does_not_publish_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'payload'
            path.write_bytes(b'abc')
            result = {'id': 'file-id', 'size': '3', 'name': 'video.mp4'}
            service = MagicMock()
            service.files.return_value.create.return_value.next_chunk.side_effect = [(None, None), (None, result)]
            context = MagicMock()
            context.__enter__.return_value = service
            media = MagicMock()
            with patch.object(drive, 'build', return_value=context), patch.object(drive, 'MediaFileUpload', return_value=media) as factory:
                self.assertEqual(drive.upload_file(path, 'video.mp4', object(), 'folder-id'), result)
                self.assertTrue(factory.call_args.kwargs['resumable'])
                self.assertEqual(service.files.return_value.create.call_args.kwargs['body'], {'name': 'video.mp4', 'parents': ['folder-id']})
                service.permissions.assert_not_called()
                media.stream.return_value.close.assert_called_once()
            service.files.return_value.create.return_value.next_chunk.side_effect = [(None, {**result, 'size': '1'})]
            with patch.object(drive, 'build', return_value=context), patch.object(drive, 'MediaFileUpload', return_value=media):
                with self.assertRaisesRegex(drive.DriveTransferError, 'unexpected file size'):
                    drive.upload_file(path, 'video.mp4', object())


if __name__ == '__main__':
    unittest.main()
