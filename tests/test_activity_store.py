import asyncio
import copy
import os
import unittest
from unittest.mock import patch

with patch.dict(os.environ, {
    'TELEGRAM_API_ID':'12345','TELEGRAM_API_HASH':'test_hash','TELEGRAM_BOT_TOKEN':'12345:test_token',
    'TELEGRAM_BOT_USERNAME':'TestGateStreamBot','OWNER_ID':'12345','DATABASE_URL':'mongodb://localhost:27017',
    'SPACE_ID':'user/app','SPACE_HOST':'user-app.hf.space',
}, clear=True):
    from bot.modules.activity import TransferActivity
    from bot.modules.activity_store import ActivityStore


class Collection:
    def __init__(self):
        self.documents = {}
        self.fail_read = False
        self.timeout_after_write = False
        self.writes = 0

    async def find_one(self, query):
        if self.fail_read: raise RuntimeError('database offline')
        return copy.deepcopy(self.documents.get(query['_id']))

    async def update_one(self, query, update, **kwargs):
        self.documents[query['_id']] = dict(_id=query['_id'], **copy.deepcopy(update['$set']))
        self.writes += 1
        if self.timeout_after_write:
            self.timeout_after_write = False
            raise asyncio.TimeoutError()


class ActivityPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_restart_retains_totals_and_closes_previous_active_transfers(self):
        database=Collection()
        first=TransferActivity(clock=lambda:0)
        download=first.start('download',500)
        first.progress(download,500)
        first.finish(download)
        media=first.start('media',1000,'private-code')
        first.progress(media,300)
        self.assertTrue(await ActivityStore(first,lambda:database).flush())
        # This is a fresh process: no old jobs, rates, filename or private code can survive.
        second=TransferActivity(clock=lambda:0)
        store=ActivityStore(second,lambda:database)
        self.assertTrue(await store.flush())
        snapshot=second.snapshot()
        self.assertEqual(snapshot['phases']['download']['bytes'],500)
        self.assertEqual(snapshot['phases']['download']['completed'],1)
        self.assertEqual(snapshot['phases']['media']['bytes'],300)
        self.assertEqual(snapshot['phases']['media']['incomplete'],1)
        self.assertEqual(snapshot['phases']['media']['active'],0)
        self.assertEqual(snapshot['phases']['media']['speed_bps'],0)
        self.assertEqual(snapshot['transfers'],[])
        self.assertNotIn('private-code',str(database.documents))
        self.assertEqual(snapshot['persistence']['state'],'saved')
        self.assertTrue(await store.flush())
        self.assertEqual(second.snapshot()['phases']['media']['incomplete'],1)
        third=TransferActivity()
        self.assertTrue(await ActivityStore(third,lambda:database).flush())
        self.assertEqual(third.snapshot()['phases']['media']['incomplete'],1)

    async def test_failed_read_never_overwrites_saved_data_and_recovers_once(self):
        database=Collection()
        database.documents['transfer_activity']=dict(phases={'download':{'bytes':1000,'completed':4}})
        database.fail_read=True
        tracker=TransferActivity()
        ident=tracker.start('download',50)
        tracker.progress(ident,50)
        tracker.finish(ident)
        store=ActivityStore(tracker,lambda:database)
        self.assertFalse(await store.flush())
        self.assertEqual(database.writes,0)
        self.assertEqual(tracker.snapshot()['persistence']['state'],'unavailable')
        database.fail_read=False
        self.assertTrue(await store.flush())
        self.assertEqual(tracker.snapshot()['phases']['download']['bytes'],1050)
        self.assertEqual(tracker.snapshot()['phases']['download']['completed'],5)
        self.assertTrue(await store.flush())
        self.assertEqual(tracker.snapshot()['phases']['download']['bytes'],1050)

    async def test_uncertain_write_retry_cannot_double_count(self):
        database=Collection()
        tracker=TransferActivity()
        ident=tracker.start('upload',200)
        tracker.progress(ident,200)
        tracker.finish(ident)
        store=ActivityStore(tracker,lambda:database)
        database.timeout_after_write=True
        self.assertFalse(await store.flush())
        self.assertTrue(await store.flush())
        restarted=TransferActivity()
        self.assertTrue(await ActivityStore(restarted,lambda:database).flush())
        self.assertEqual(restarted.snapshot()['phases']['upload']['bytes'],200)
        self.assertEqual(restarted.snapshot()['phases']['upload']['completed'],1)

    async def test_first_upgrade_recovers_legacy_bandwidth_but_does_not_invent_counts(self):
        database=Collection()
        database.documents['bandwidth']=dict(bytes_streamed=123456,requests=20)
        tracker=TransferActivity()
        self.assertTrue(await ActivityStore(tracker,lambda:database).flush())
        snapshot=tracker.snapshot()
        self.assertEqual(snapshot['phases']['media']['bytes'],123456)
        self.assertEqual(snapshot['phases']['media']['completed'],0)
        self.assertEqual(snapshot['phases']['download']['bytes'],0)

    async def test_shutdown_event_flushes_changes_without_waiting_for_poll_interval(self):
        database=Collection()
        tracker=TransferActivity()
        store=ActivityStore(tracker,lambda:database)
        stop=asyncio.Event()
        task=asyncio.create_task(store.run(stop))
        await asyncio.sleep(0)
        ident=tracker.start('download',123)
        tracker.progress(ident,123)
        tracker.finish(ident)
        stop.set()
        await asyncio.wait_for(task,1)
        saved=database.documents['transfer_activity']['phases']['download']
        self.assertEqual(saved['bytes'],123)
        self.assertEqual(saved['completed'],1)

    async def test_real_web_lifecycle_restores_totals_before_serving_activity_api(self):
        from bot.server import instance, main
        import bot.server as server_module
        database=Collection()
        first=TransferActivity()
        with patch.object(server_module,'activity_store',ActivityStore(first,lambda:database)),patch.object(main,'activity',first):
            async with instance.test_app() as app:
                ident=first.start('upload',4096)
                first.progress(ident,4096)
                first.finish(ident)
                response=await app.test_client().get('/api/activity')
                self.assertEqual((await response.get_json())['phases']['upload']['bytes'],4096)
        second=TransferActivity()
        with patch.object(server_module,'activity_store',ActivityStore(second,lambda:database)),patch.object(main,'activity',second):
            async with instance.test_app() as app:
                response=await app.test_client().get('/api/activity')
                result=await response.get_json()
                self.assertEqual(result['phases']['upload']['bytes'],4096)
                self.assertEqual(result['phases']['upload']['completed'],1)
                self.assertEqual(result['phases']['upload']['active'],0)
                self.assertEqual(result['persistence']['state'],'saved')
                self.assertIn('no-store',response.headers['Cache-Control'])

    async def test_new_download_totals_survive_restart_with_older_checkpoint_fields(self):
        database=Collection()
        database.documents['transfer_activity']=dict(phases={'media':{'completed':20,'bytes':100}})
        tracker=TransferActivity()
        store=ActivityStore(tracker,lambda:database)
        self.assertTrue(await store.flush())
        tracker.downloaded_range(('file',b'client'),'single',0,6,6)
        tracker.downloaded_range(('zip',b'client'),'zip',0,3,6)
        tracker.downloaded_range(('zip',b'client'),'zip',3,6,6)
        self.assertTrue(await store.flush())
        restarted=TransferActivity()
        self.assertTrue(await ActivityStore(restarted,lambda:database).flush())
        phase=restarted.snapshot()['phases']['media']
        self.assertEqual((phase['single_downloads'],phase['zip_downloads'],phase['completed'],phase['bytes']),(1,1,20,100))
