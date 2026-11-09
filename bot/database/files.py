import asyncio
from secrets import token_hex
from datetime import datetime
from bot.config import Telegram, Database
from bot.database import db

_cached_omni_counts = None
_cached_omni_time = 0.0

async def save_file(
    channel_id: int,
    message_id: int,
    file_name: str,
    file_size: int,
    mime_type: str,
    user_id: int,
    category: str = 'movies',
    title: str = None,
    duration: int = 0,
    duration_formatted: str = "N/A",
    metadata: dict = None,
    *, personal=False, personal_index_id=None,
) -> dict:
    """Saves a single movie or direct file."""
    collection = db.get_collection(category)
    
    # Link records remain separate from public records of the same channel post.
    existing = await collection.find_one({
        'channel_id': channel_id,
        'message_id': message_id,
        'catalog_section': 'personal' if personal else {'$nin': ['index', 'personal']}
    })
    if existing:
        # Auto-detect if file in channel post was replaced or modified
        if (
            file_size != existing.get('file_size')
            or file_name != existing.get('file_name')
            or duration != existing.get('duration')
        ):
            update_data = {
                'file_name': file_name,
                'file_size': file_size,
                'mime_type': mime_type,
                'duration': duration,
                'duration_formatted': duration_formatted,
                'title': title or file_name,
                'updated_at': datetime.utcnow()
            }
            await collection.update_one({'_id': existing['_id']}, {'$set': update_data})
            existing.update(update_data)
            try:
                from bot.modules.cache import evict_file_chunks
                evict_file_chunks(existing['_id'])
            except Exception:
                pass
        return existing

    code = token_hex(Telegram.SECRET_CODE_LENGTH)
    doc = {
        '_id': code,
        'code': code,
        'channel_id': channel_id,
        'message_id': message_id,
        'file_name': file_name,
        'file_size': file_size,
        'mime_type': mime_type,
        'duration': duration,
        'duration_formatted': duration_formatted,
        'user_id': user_id,
        'category': category.lower(),
        'catalog_section': 'personal' if personal else 'link',
        'title': title or file_name,
        'created_at': datetime.utcnow()
    }
    
    if personal_index_id is not None:
        doc['personal_index_id'] = personal_index_id
    if metadata:
        doc['metadata'] = metadata
    await collection.insert_one(doc)
    return doc

async def get_file(code: str, category_hint: str = None) -> dict | None:
    """Resolves a file by its unique code, supporting both single documents and episodes inside batch documents."""
    if not code:
        return None

    # 1. Search in single file collections / direct document match across all collections
    for col in (db.movies, db.direct_files, db.anime, db.webseries):
        if col is not None:
            doc = await col.find_one({'_id': code})
            if doc:
                # If it's a single file (not a batch container with episodes array)
                if 'episodes' not in doc:
                    return doc
                # If the batch _id itself was requested, return it
                return doc

    # 2. Search inside batch documents (anime, webseries)
    for col in (db.movies, db.anime, db.webseries):
        if col is not None:
            batch_doc = await col.find_one({'episodes.code': code})
            if batch_doc:
                for ep in batch_doc.get('episodes', []):
                    if ep.get('code') == code:
                        return {
                            '_id': ep['code'],
                            'code': ep['code'],
                            'channel_id': batch_doc['channel_id'],
                            'message_id': ep['message_id'],
                            'file_name': ep['file_name'],
                            'file_size': ep['file_size'],
                            'mime_type': ep['mime_type'],
                            'duration': ep.get('duration', 0),
                            'duration_formatted': ep.get('duration_formatted', 'N/A'),
                            'category': batch_doc.get('category', 'anime'),
                            'user_id': batch_doc.get('user_id'),
                            'batch_id': batch_doc['_id']
                        }

    return None

async def delete_file(code: str) -> bool:
    """Deletes a file or removes an episode from a batch."""
    if not code:
        return False
        
    # Delete from single file collections or direct _id match
    for col in (db.movies, db.direct_files, db.anime, db.webseries):
        if col is not None:
            res = await col.delete_one({'_id': code})
            if res.deleted_count > 0:
                try:
                    from bot.modules.cache import evict_file_chunks
                    evict_file_chunks(code)
                except Exception:
                    pass
                return True

    # Pull single episode from batch if it was part of an episodes array
    for col in (db.movies, db.anime, db.webseries):
        if col is not None:
            res = await col.update_one(
                {'episodes.code': code},
                {'$pull': {'episodes': {'code': code}}}
            )
            if res.modified_count > 0:
                try:
                    from bot.modules.cache import evict_file_chunks
                    evict_file_chunks(code)
                except Exception:
                    pass
                return True

    return False

async def add_bandwidth_bytes(byte_count: int):
    try:
        if db.db is not None and byte_count > 0:
            await db.db['analytics'].update_one(
                {'_id': 'bandwidth'},
                {'$inc': {'bytes_streamed': byte_count, 'requests': 1}},
                upsert=True
            )
    except Exception:
        pass

async def get_bandwidth_stats() -> tuple[int, int]:
    try:
        if db.db is not None:
            doc = await db.db['analytics'].find_one({'_id': 'bandwidth'})
            if doc:
                return doc.get('bytes_streamed', 0), doc.get('requests', 0)
    except Exception:
        pass
    return 0, 0

async def get_stats() -> dict:
    stats = {
        'movies': 0,
        'movie_titles': 0,
        'anime': 0,
        'anime_titles': 0,
        'anime_episodes': 0,
        'webseries': 0,
        'webseries_titles': 0,
        'webseries_episodes': 0,
        'direct_files': 0,
        'total_titles': 0
    }
    
    if db.movies is not None:
        stats['movie_titles'] = await db.movies.count_documents({})
        stats['movies'] = stats['movie_titles']
    stats['local_movies'] = stats['movies']
    if db.direct_files is not None:
        stats['direct_files'] = await db.direct_files.count_documents({})

    # Count titles and total playable files/episodes across anime and webseries
    for name, col in (('anime', db.anime), ('webseries', db.webseries)):
        if col is not None:
            stats[f'{name}_titles'] = await col.count_documents({})
            pipeline = [
                {'$project': {
                    'count': {
                        '$cond': [
                            {'$isArray': '$episodes'},
                            {'$size': {'$ifNull': ['$episodes', []]}},
                            1
                        ]
                    }
                }},
                {'$group': {'_id': None, 'total': {'$sum': '$count'}}}
            ]
            cursor = col.aggregate(pipeline)
            docs = await cursor.to_list(length=1)
            tot_eps = docs[0]['total'] if docs else 0
            stats[f'{name}_episodes'] = tot_eps
            stats[name] = tot_eps

    # Fetch exact authoritative counts from OmniArchiverDB (225 Anime Franchises, 191 Movies, 85 Web Series)
    global _cached_omni_counts, _cached_omni_time
    import time
    now = time.time()

    omni_counts = None
    if _cached_omni_counts and (now - _cached_omni_time < 300):
        omni_counts = _cached_omni_counts
    else:
        target_omni_db = getattr(db, 'omni_db', None)

        # Lazy-init if db.connect() was bypassed or omni_db is not yet set
        if target_omni_db is None and getattr(Database, 'OMNI_MONGO_URI', None):
            try:
                from motor.motor_asyncio import AsyncIOMotorClient
                omni_client = AsyncIOMotorClient(Database.OMNI_MONGO_URI, serverSelectionTimeoutMS=7000)
                target_omni_db = omni_client['OmniArchiverDB']
            except Exception:
                pass

        if target_omni_db is not None:
            try:
                a_task = target_omni_db['anime_index'].count_documents({})
                m_task = target_omni_db['movies_index'].count_documents({})
                s_task = target_omni_db['series_index'].count_documents({})
                omni_counts = await asyncio.wait_for(asyncio.gather(a_task, m_task, s_task), timeout=7.0)
                if omni_counts:
                    _cached_omni_counts = omni_counts
                    _cached_omni_time = now
            except Exception as e:
                import logging
                logging.getLogger('bot').warning("Could not refresh upstream catalog counts: %s", e)
                if _cached_omni_counts:
                    omni_counts = _cached_omni_counts
                else:
                    omni_counts = None

    if omni_counts:
        stats['anime_franchises'] = omni_counts[0]  # Exactly 225 unique Anime Franchises
        if omni_counts[1] > 0:
            stats['movie_titles'] = omni_counts[1]  # Exactly 191 Live-Action Films
            stats['movies'] = omni_counts[1]
        if omni_counts[2] > 0:
            stats['webseries_titles'] = omni_counts[2]  # Exactly 85 Web Series
    else:
        stats['anime_franchises'] = stats.get('anime_titles', 0)

    # Total titles = Parent Anime franchises (225) + Movies (191) + Web Series (85) = 501
    stats['total_titles'] = (
        stats['anime_franchises'] + stats['movie_titles'] + stats['webseries_titles']
    )
    return stats
