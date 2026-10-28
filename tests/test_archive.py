import unittest
from quart import Response
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('archive_under_test', Path(__file__).parents[1] / 'bot/modules/archive.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
stream_archive = module.stream_archive


class ArchiveTests(unittest.IsolatedAsyncioTestCase):
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
