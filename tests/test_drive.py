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
            updates = []
            path, name = await drive.download_file('https://example.com/a?signature=a%2Fb', directory, 10,
                progress=lambda done, total: updates.append((done,total)))
            self.assertEqual(updates, [(0,6),(3,6),(6,6)])
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
            service.files.return_value.create.return_value.next_chunk.side_effect = [(SimpleNamespace(resumable_progress=2), None), (None, result)]
            context = MagicMock()
            context.__enter__.return_value = service
            media = MagicMock()
            with patch.object(drive, 'build', return_value=context), patch.object(drive, 'MediaFileUpload', return_value=media) as factory:
                updates = []
                self.assertEqual(drive.upload_file(path, 'video.mp4', object(), 'folder-id',
                    progress=lambda done, total: updates.append((done,total))), result)
                self.assertEqual(updates, [(2,3),(3,3)])
                self.assertTrue(factory.call_args.kwargs['resumable'])
                self.assertEqual(service.files.return_value.create.call_args.kwargs['body'], {'name': 'video.mp4', 'parents': ['folder-id']})
                service.permissions.assert_not_called()
                media.stream.return_value.close.assert_called_once()
            service.files.return_value.create.return_value.next_chunk.side_effect = [(None, {**result, 'size': '1'})]
            with patch.object(drive, 'build', return_value=context), patch.object(drive, 'MediaFileUpload', return_value=media):
                with self.assertRaisesRegex(drive.DriveTransferError, 'unexpected file size'):
                    drive.upload_file(path, 'video.mp4', object())


    def test_drive_share_links_and_index_paths(self):
        ident = 'abc1234567890'
        for link in [ident, f'https://drive.google.com/file/d/{ident}/view',
                     f'https://drive.google.com/drive/u/0/folders/{ident}',
                     f'https://drive.google.com/open?id={ident}']:
            self.assertEqual(drive.drive_source(link), (ident, ''))
        self.assertEqual(drive.drive_source(f'https://drive.google.com/file/d/{ident}/view?resourcekey=0-key'), (ident, '0-key'))
        for link in ['https://drive.google.com.evil.com/file/d/'+ident,
                     'https://user:pass@drive.google.com/file/d/'+ident, 'https://example.com/file', 'bad-id']:
            with self.assertRaises(drive.DriveTransferError): drive.drive_source(link)
        self.assertEqual(drive.drive_index_link('https://index.example/0:/Mirror/', 'বাংলা #1?.mkv'),
            'https://index.example/0:/Mirror/%E0%A6%AC%E0%A6%BE%E0%A6%82%E0%A6%B2%E0%A6%BE%20%231%3F.mkv')
        self.assertEqual(drive.drive_index_link('https://index.example/1:', 'Season 1', True),
            'https://index.example/1:/Season%201/')
        self.assertIsNone(drive.drive_index_link('', 'file'))
        self.assertIsNone(drive.drive_index_link('https://index.example/0:/', 'bad/name'))
        for base in ['http://index.example/0:/', 'https://user:secret@index.example/0:/',
                     'https://index.example/', 'https://index.example/0:/?token=secret']:
            with self.assertRaises(drive.DriveTransferError): drive.drive_index_link(base, 'file')

    def clone_service(self):
        service = MagicMock()
        root = dict(id='source123456', name='Season 1', mimeType=drive.FOLDER_MIME)
        child = dict(id='child1234567', name='Subfolder', mimeType=drive.FOLDER_MIME)
        file = dict(id='media1234567', name='One.mkv', mimeType='video/x-matroska', size='3', resourceKey='0-media')
        api = service.files.return_value
        for method in ('get','list','create','copy'):
            getattr(api, method).return_value.headers = {}
        api.get.return_value.execute.return_value = root
        api.list.return_value.execute.side_effect = [dict(files=[child],nextPageToken='second'), dict(files=[file]), dict(files=[])]
        api.create.return_value.execute.side_effect = [dict(root,id='newroot'), dict(child,id='newchild')]
        api.copy.return_value.execute.return_value = dict(file,id='newmedia')
        context = MagicMock()
        context.__enter__.return_value = service
        return api, context

    def test_clone_tree_pagination_resource_keys_and_destination(self):
        api, context = self.clone_service()
        with patch.object(drive, 'build', return_value=context):
            updates=[]
            result=drive.clone_drive('https://drive.google.com/drive/folders/source123456?resourcekey=root-key', object(), 'destination',
                progress=lambda *args:updates.append(args))
        self.assertEqual((result['id'],result['file_count']),('newroot',1))
        self.assertEqual(api.list.call_count,3)
        self.assertEqual(api.list.call_args_list[1].kwargs['pageToken'],'second')
        self.assertEqual(api.create.call_args_list[0].kwargs['body']['parents'],['destination'])
        self.assertEqual(api.create.call_args_list[1].kwargs['body']['parents'],['newroot'])
        self.assertEqual(api.copy.call_args.kwargs['body']['parents'],['newroot'])
        self.assertEqual(api.copy.return_value.headers['X-Goog-Drive-Resource-Keys'],'media1234567/0-media')
        self.assertEqual(api.copy.return_value.execute.call_args.kwargs['num_retries'],0)
        self.assertEqual(updates[-1],('Cloning',3,3))
        api.update.assert_not_called()
        service=context.__enter__.return_value
        service.permissions.assert_not_called()

    def test_clone_preflight_rejects_limits_shortcuts_and_restricted_files_before_writes(self):
        for case in ('limit','shortcut','copy-disabled','incomplete'):
            api,context=self.clone_service()
            if case!='limit':
                item=dict(id='media1234567',name='file',mimeType='video/mp4',capabilities={'canCopy':False})
                if case=='shortcut': item['mimeType']=drive.SHORTCUT_MIME
                api.list.return_value.execute.side_effect=[dict(files=[item],incompleteSearch=case=='incomplete')]
            with self.subTest(case=case),patch.object(drive,'build',return_value=context):
                with self.assertRaises(drive.DriveTransferError):
                    drive.clone_drive('source123456',object(),max_items=1 if case=='limit' else 10)
                api.create.assert_not_called()
                api.copy.assert_not_called()

    def test_cancelled_or_failed_clone_trashes_only_the_new_root(self):
        from threading import Event
        for cancel in (True,False):
            api,context=self.clone_service()
            stop=Event()
            def update(stage,done,total):
                if stage=='Cloning' and done==1 and cancel: stop.set()
            if not cancel: api.copy.return_value.execute.side_effect=RuntimeError('transport failed')
            with self.subTest(cancel=cancel),patch.object(drive,'build',return_value=context):
                with self.assertRaises((drive.DriveTransferError,RuntimeError)):
                    drive.clone_drive('source123456',object(),progress=update,cancelled=stop)
                self.assertEqual(api.update.call_args.kwargs['fileId'],'newroot')
                self.assertEqual(api.update.call_args.kwargs['body'],{'trashed':True})

    def test_clone_cleanup_failure_is_reported_and_upload_cancels_before_network_write(self):
        from threading import Event
        api,context=self.clone_service()
        api.copy.return_value.execute.side_effect=RuntimeError('failure')
        api.update.return_value.execute.side_effect=RuntimeError('cannot trash')
        with patch.object(drive,'build',return_value=context),self.assertRaisesRegex(drive.DriveTransferError,'partial copy'):
            drive.clone_drive('source123456',object())
        stop=Event();stop.set()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'payload';path.write_bytes(b'abc')
            with patch.object(drive,'build',return_value=context),self.assertRaisesRegex(drive.DriveTransferError,'cancelled'):
                drive.upload_file(path,'file.bin',object(),cancelled=stop)
        api.create.return_value.next_chunk.assert_not_called()

    def test_single_file_clone_uses_my_drive_root_when_destination_is_blank(self):
        api, context = self.clone_service()
        item=dict(id='source123456',name='video.mp4',mimeType='video/mp4')
        api.get.return_value.execute.return_value=item
        api.copy.return_value.execute.return_value=dict(item,id='newfile')
        with patch.object(drive,'build',return_value=context):
            result=drive.clone_drive('source123456',object())
        self.assertEqual(result['file_count'],1)
        self.assertEqual(api.copy.call_args.kwargs['body']['parents'],['root'])
        api.create.assert_not_called()
        api.list.assert_not_called()
