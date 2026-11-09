from secrets import token_hex
from datetime import datetime
from bot.config import Telegram
from bot.database import db

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
    *, personal=False, personal_index_id=None, source_key=None,
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
    if source_key is not None:
        doc['source_key'] = source_key
    if metadata:
        doc['metadata'] = metadata
    await collection.insert_one(doc)
    return doc

async def get_group_upload(source_key, user_id):
    return await db.direct_files.find_one(
        {'source_key': source_key, 'user_id': user_id, 'catalog_section': 'personal'})


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
