"""Bounded, resumable channel scans using bot-compatible get_messages calls."""
import asyncio
import time
from datetime import datetime, timezone
from secrets import token_hex

import aiohttp
from hydrogram.errors import FloodWait
from pymongo import ReturnDocument
from bot.config import Telegram
from bot.database import db
from bot.modules.metadata import Classifier
from bot.modules.telegram import get_file_properties, is_media_message


async def fetch_messages(client, channel_id, ids, stop):
    while not stop.is_set():
        try:
            return await asyncio.wait_for(client.get_messages(channel_id, message_ids=ids), timeout=45)
        except FloodWait as error:
            # Wait asynchronously and allow /cancelindex to interrupt a long Telegram cooldown.
            try:
                await asyncio.wait_for(stop.wait(), timeout=max(1, error.value))
            except asyncio.TimeoutError:
                pass
    return []


async def save_scanned_file(message, owner_id, classifier):
    name, size, mime, duration, formatted = get_file_properties(message)
    channel_id, message_id = message.chat.id, message.id
    collections = [db.movies, db.anime, db.webseries, db.direct_files]
    existing, old_collection = None, None
    for collection in collections:
        existing = await collection.find_one({'channel_id': channel_id, 'message_id': message_id, 'catalog_section': 'index'})
        if existing:
            old_collection = collection
            break
    if existing and existing.get('file_name') == name and existing.get('file_size') == size and existing.get('metadata', {}).get('status') == 'matched':
        return 'existing', existing.get('category', 'direct_files')
    video = mime.startswith('video/') or name.lower().endswith(('.mp4', '.mkv', '.webm', '.avi', '.mov', '.m4v', '.ts'))
    metadata = await classifier.classify(name) if video else dict(category='direct_files', title=name, status='not_video', source='')
    category = metadata['category']
    collection = db.get_collection(category)
    code = existing['_id'] if existing else token_hex(Telegram.SECRET_CODE_LENGTH)
    values = dict(code=code, channel_id=channel_id, message_id=message_id, file_name=name,
        file_size=size, mime_type=mime, duration=duration, duration_formatted=formatted,
        user_id=owner_id, category=category, catalog_section='index', title=metadata['title'], metadata=metadata,
        updated_at=datetime.now(timezone.utc))
    if old_collection is not None and old_collection.name != collection.name:
        # Insert before removing the old category, preserving download codes during reclassification.
        await collection.update_one({'_id': code}, {'$set': values, '$setOnInsert': {'created_at': existing.get('created_at', datetime.now(timezone.utc))}}, upsert=True)
        await old_collection.delete_one({'_id': code})
    else:
        # Repeated scans reuse the same public record and never publish an existing /link record.
        await collection.find_one_and_update(
            {'channel_id': channel_id, 'message_id': message_id, 'catalog_section': 'index'},
            {'$set': {key: value for key, value in values.items() if key != 'code'},
             '$setOnInsert': {'_id': code, 'code': code, 'created_at': datetime.now(timezone.utc)}},
            upsert=True, return_document=ReturnDocument.AFTER)
    return ('updated' if existing else 'added'), category


async def scan_channel(client, channel_id, end_id, owner_id, stop, report, rescan=False):
    state_collection = db.db['index_channels']
    state = await state_collection.find_one({'_id': channel_id}) or {}
    start = 1 if rescan else int(state.get('last_scanned', 0)) + 1
    counts = dict(added=0, updated=0, existing=0, skipped=0, unmatched=0)
    last = start - 1
    last_report = 0
    async with aiohttp.ClientSession() as session:
        classifier = Classifier(session)
        for offset in range(start, end_id + 1, 50):
            if stop.is_set(): break
            ids = list(range(offset, min(offset + 50, end_id + 1)))
            messages = await fetch_messages(client, channel_id, ids, stop)
            by_id = {m.id: m for m in messages if m and not getattr(m, 'empty', False)}
            for ident in ids:
                if stop.is_set(): break
                message = by_id.get(ident)
                if message and is_media_message(message):
                    outcome, category = await save_scanned_file(message, owner_id, classifier)
                    counts[outcome] += 1
                    if category == 'direct_files': counts['unmatched'] += 1
                else:
                    counts['skipped'] += 1
                last = ident
                # Per-message checkpoint prevents a crash midway through a metadata lookup
                # from claiming that the rest of the fetched block was already indexed.
                await state_collection.update_one({'_id': channel_id}, {'$max': {'last_scanned': ident, 'latest_seen': end_id}}, upsert=True)
                if time.monotonic() - last_report >= 10:
                    await report(last, end_id, counts, classifier.unavailable)
                    last_report = time.monotonic()
            await report(last, end_id, counts, classifier.unavailable)
            await asyncio.sleep(.3)
    return last, counts
