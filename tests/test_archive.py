import unittest
from quart import Response
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('archive_under_test', Path(__file__).parents[1] / 'bot/modules/archive.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
stream_archive = module.stream_archive


class ArchiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_content_length_for_ascii_unicode_and_multiple_files(self):
        from zipfile import ZipFile
        import io
        for files in [[], [dict(code='one', file_name='a.mp4', file_size=11)],
                      [dict(code='one', file_name='বাংলা.mp4', file_size=11), dict(code='two', file_name='a.mp4', file_size=11)]]:
            async def download(code):
                async def body(): yield b'hello world'
                return Response(body(), headers={'Content-Length':'11'})
            data = b''.join([chunk async for chunk in stream_archive(files,download)])
            self.assertEqual(len(data),module.archive_size(files))
            with ZipFile(io.BytesIO(data)) as archive:
                self.assertIsNone(archive.testzip())

    def test_zip64_length_includes_large_entry_and_offset_extensions(self):
        from zipfile import ZipFile, ZIP64_LIMIT
        class VirtualBytes(bytes):
            def __new__(cls, size):
                value = super().__new__(cls, b'')
                value.size = size
                return value
            def __len__(self): return self.size
        files=[dict(file_name='large.mkv',file_size=ZIP64_LIMIT+100),dict(file_name='small.mp4',file_size=11)]
        sink=module._Sink()
        with ZipFile(sink,'w') as archive:
            for index,item in enumerate(files,1):
                with archive.open(module.archive_name(index,item),'w',force_zip64=True) as entry:
                    entry.write(VirtualBytes(item['file_size']))
        self.assertEqual(sink.tell(),module.archive_size(files))

    async def test_truncated_source_cannot_produce_successful_zip(self):
        async def download(code):
            async def body():
                yield b'partial'
            return Response(body(), headers={'Content-Length': '100'})
        with self.assertRaisesRegex(RuntimeError, 'incomplete'):
            async for _ in stream_archive([dict(code='one', file_name='file.mp4')], download):
                pass

    async def test_cancel_closes_active_source_without_starting_other_files(self):
        closed, started = [], []

        async def download(code):
            started.append(code)
            async def body():
                try:
                    yield b'first'
                    yield b'second'
                finally:
                    closed.append(code)
            return Response(body(), headers={'Content-Length': '11'})

        stream = stream_archive([dict(code='one'), dict(code='two')], download)
        async for chunk in stream:
            if chunk == b'first':
                break
        await stream.aclose()
        self.assertEqual(started, ['one'])
        self.assertEqual(closed, ['one'])
