"""Periodic MongoDB checkpoints; transfer chunks never wait for analytics writes."""
import asyncio
import logging
from datetime import datetime, timezone

from bot.database import db
from bot.modules.activity import activity

logger = logging.getLogger('bot')


class ActivityStore:
    # ponytail: one writer per deployment; use per-process aggregation for replicas.
    def __init__(self, tracker, collection):
        self.tracker = tracker
        self.collection = collection
        self.loaded = False
        self.last_saved = None
        self.lock = asyncio.Lock()

    async def flush(self):
        async with self.lock:
            try:
                collection = self.collection()
                if not self.loaded:
                    document = await collection.find_one({'_id': 'transfer_activity'})
                    phases = document['phases'] if document else {}
                    if not document:
                        current = self.tracker.checkpoint()
                        # First upgrade can recover old media bytes, before new transfers start.
                        # Other historical counts were never recorded and cannot be reconstructed.
                        if not any(p['bytes'] or p['active'] or p['completed'] or p['failed'] or p['incomplete'] for p in current.values()):
                            legacy = await collection.find_one({'_id': 'bandwidth'}) or {}
                            phases = {'media': {'bytes': max(0, int(legacy.get('bytes_streamed', 0)))}}
                    self.tracker.restore(phases)
                    self.loaded = True
                phases = self.tracker.checkpoint()
                if phases != self.last_saved:
                    timestamp = datetime.now(timezone.utc)
                    await collection.update_one({'_id': 'transfer_activity'},
                        {'$set': {'phases': phases, 'updated_at': timestamp}}, upsert=True)
                    self.last_saved = phases
                    self.tracker.saved_state('saved', timestamp.isoformat())
                else:
                    self.tracker.saved_state('saved')
                return True
            except Exception as error:
                # A failed read must never overwrite saved totals with zero.
                self.tracker.saved_state('unavailable')
                logger.warning('Activity totals could not be saved/restored (%s).', type(error).__name__)
                return False

    async def run(self, stop):
        try:
            while not stop.is_set():
                await self.flush()
                try:
                    await asyncio.wait_for(stop.wait(), timeout=5)
                except asyncio.TimeoutError:
                    pass
        finally:
            await self.flush()


def analytics_collection():
    if db.db is None:
        raise RuntimeError('Database is unavailable')
    return db.db['analytics']


activity_store = ActivityStore(activity, analytics_collection)
