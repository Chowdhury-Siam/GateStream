"""Bounded, paginated listing across individual files and batch episodes."""
import re
from bot.database import db

CATEGORIES = ('movies', 'anime', 'webseries', 'direct_files')
PAGE_SIZE = 30


def _pipeline(category, query):
    fields = {
        '_id': 0, 'created_at': 1, 'category': {'$literal': category},
        'code': {'$cond': ['$_is_batch', '$episodes.code', {'$ifNull': ['$code', '$_id']}]},
        'batch_title': {'$cond': ['$_is_batch', {'$ifNull': ['$title', '']}, '']},
    }
    for field, fallback in [('file_name', 'Untitled file'), ('file_size', 0), ('duration_formatted', 'N/A')]:
        fields[field] = {'$cond': ['$_is_batch', {'$ifNull': [f'$episodes.{field}', fallback]},
                                  {'$ifNull': [f'${field}', fallback]}]}
    pipeline = [
        {'$addFields': {'_is_batch': {'$isArray': '$episodes'}}},
        {'$unwind': {'path': '$episodes', 'preserveNullAndEmptyArrays': True}},
        {'$project': fields},
        {'$match': {'code': {'$type': 'string', '$ne': ''}}},
    ]
    if query:
        expression = {'$regex': re.escape(query), '$options': 'i'}
        pipeline.append({'$match': {'$or': [{'file_name': expression}, {'batch_title': expression}]}})
    pipeline.append({'$sort': {'created_at': -1, 'code': 1}})
    return pipeline


async def list_files(page=1, query='', category='all'):
    offset = (page - 1) * PAGE_SIZE
    files, total = [], 0
    for name in CATEGORIES:
        if category != 'all' and category != name:
            continue
        collection = getattr(db, name)
        if collection is None:
            continue
        facet = {'total': [{'$count': 'value'}]}
        if len(files) < PAGE_SIZE:
            facet['items'] = [{'$skip': offset}, {'$limit': PAGE_SIZE - len(files)}]
        result = await collection.aggregate(_pipeline(name, query) + [{'$facet': facet}]).to_list(length=1)
        result = result[0] if result else {}
        count = result.get('total', [{}])[0].get('value', 0) if result.get('total') else 0
        total += count
        files.extend(result.get('items', []))
        offset = max(0, offset - count)
    return dict(files=files, total=total, page=page, pages=max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE))
