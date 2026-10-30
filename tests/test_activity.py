import asyncio
import importlib.util
import json
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

spec = importlib.util.spec_from_file_location('transfer_activity', Path(__file__).resolve().parents[1] / 'bot/modules/activity.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class ActivityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 0
        self.activity = module.TransferActivity(clock=lambda: self.now)

    async def test_monotonic_bytes_percentage_eta_and_rate_decay(self):
        ident = self.activity.start('download', 2000)
        self.now = 2
        for done in [1000,1500,1500,1000]: self.activity.progress(ident,done)
        data = self.activity.snapshot()
        self.assertEqual(data['phases']['download']['bytes'],1500)
        self.assertEqual(data['transfers'][0]['percent'],75)
        self.assertEqual(data['transfers'][0]['speed_bps'],750)
        self.assertAlmostEqual(data['transfers'][0]['eta_seconds'],2/3)
        self.activity.finish(ident)
        self.activity.finish(ident)
        self.now = 10
        phase = self.activity.snapshot()['phases']['download']
        self.assertEqual((phase['completed'],phase['active'],phase['speed_bps']),(1,0,0))

    async def test_distinct_links_connections_and_privacy(self):
        for key in ['private-code-one','private-code-one','private-code-two']:
            self.activity.start('media',100,key)
        data = self.activity.snapshot()
        self.assertEqual(data['active_media_links'],2)
        self.assertEqual(data['phases']['media']['active'],3)
        self.assertNotIn('private-code',json.dumps(data))

    async def test_unknown_size_is_not_reported_as_a_percentage(self):
        ident = self.activity.start('download')
        self.activity.progress(ident,10)
        job = self.activity.snapshot()['transfers'][0]
        for field in ['total_bytes','percent','eta_seconds']: self.assertIsNone(job[field])
        self.activity.progress(ident,20,100)
        self.assertEqual(self.activity.snapshot()['transfers'][0]['percent'],20)

    async def test_thread_safety_and_bounded_rows(self):
        def upload(_):
            with self.activity.transfer('upload',10) as ident:
                self.activity.progress(ident,5)
                self.activity.progress(ident,10)
        with ThreadPoolExecutor(max_workers=4) as pool: list(pool.map(upload,range(32)))
        phase = self.activity.snapshot()['phases']['upload']
        self.assertEqual((phase['bytes'],phase['completed'],phase['active']),(320,32,0))
        for _ in range(25): self.activity.start('download')
        data = self.activity.snapshot()
        self.assertEqual((len(data['transfers']),data['hidden_transfers']),(20,5))

    async def test_failure_and_cancellation_cleanup(self):
        with self.assertRaises(RuntimeError):
            with self.activity.transfer('upload'): raise RuntimeError('failure')
        with self.assertRaises(asyncio.CancelledError):
            with self.activity.transfer('download'): raise asyncio.CancelledError()
        data = self.activity.snapshot()
        self.assertEqual(data['phases']['upload']['failed'],1)
        self.assertEqual(data['phases']['download']['incomplete'],1)
        self.assertEqual(data['transfers'],[])

    async def test_stream_completion_and_disconnect(self):
        closed = []
        async def source():
            try:
                yield b'abc'
                yield b'def'
            finally: closed.append(True)
        stream = self.activity.track_stream(source(),6,'private-code')
        self.assertEqual(await anext(stream),b'abc')
        await stream.aclose()
        self.assertTrue(closed)
        self.assertEqual(self.activity.snapshot()['phases']['media']['incomplete'],1)
        result = b''.join([chunk async for chunk in self.activity.track_stream(source(),6,'private-code')])
        self.assertEqual(result,b'abcdef')
        phase = self.activity.snapshot()['phases']['media']
        self.assertEqual((phase['bytes'],phase['completed'],phase['active']),(9,1,0))

    async def test_stream_error_keeps_partial_bytes(self):
        async def source():
            yield b'abc'
            raise RuntimeError('failure')
        with self.assertRaises(RuntimeError):
            async for _ in self.activity.track_stream(source(),10,'private-code'): pass
        phase = self.activity.snapshot()['phases']['media']
        self.assertEqual((phase['bytes'],phase['failed'],phase['active']),(3,1,0))

    async def test_download_counts_merge_ranges_and_exclude_incomplete_streams(self):
        async def stream(data): yield data
        async def serve(start,data,kind='single',key='file'):
            return b''.join([chunk async for chunk in self.activity.track_stream(
                stream(data),len(data),key,download=(kind,start,6,b'client'))])
        await serve(2,b'cd')
        await serve(0,b'abc')
        self.assertEqual(self.activity.snapshot()['phases']['media']['single_downloads'],0)
        await serve(4,b'ef')
        await serve(4,b'ef')
        await serve(0,b'ab')
        await serve(0,b'abcdef',kind='zip',key='zip-token')
        phase=self.activity.snapshot()['phases']['media']
        self.assertEqual((phase['single_downloads'],phase['zip_downloads']),(1,1))
        restarted=module.TransferActivity()
        restarted.restore(self.activity.checkpoint())
        self.assertEqual(restarted.snapshot()['phases']['media']['single_downloads'],1)
        self.assertFalse(restarted.download_ranges)
        self.now=20
        await serve(0,b'abcdef')
        self.assertEqual(self.activity.snapshot()['phases']['media']['single_downloads'],2)
        pending=self.activity.track_stream(stream(b'ab'),6,'other',download=('single',0,6,b'client'))
        async for _ in pending: pass
        self.assertEqual(self.activity.snapshot()['phases']['media']['single_downloads'],2)
