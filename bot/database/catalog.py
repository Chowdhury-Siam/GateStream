"""Bounded, paginated listing across individual files and batch episodes."""
import re
from bot.database import db

CATEGORIES = ('movies', 'anime', 'webseries', 'direct_files')
PAGE_SIZE = 30


def _pipeline(category, query, section='index'):
    # Old records have no source flag: batches are Index; single files are Link.
    source = {'$or': [{'catalog_section': 'index'},
        {'catalog_section': {'$exists': False}, 'episodes': {'$type': 'array'}}]}
    if section == 'link':
        source = {'$nor': [source]}
    fields = {
        '_id': 0, 'created_at': 1, 'category': {'$literal': category},
        'metadata_source': {'$ifNull': ['$metadata.source', '']},
        'metadata_status': {'$ifNull': ['$metadata.status', '']},
        'code': {'$cond': ['$_is_batch', '$episodes.code', {'$ifNull': ['$code', '$_id']}]},
        'batch_title': {'$cond': ['$_is_batch', {'$ifNull': ['$title', '']}, '']},
    }
    for field, fallback in [('file_name', 'Untitled file'), ('file_size', 0), ('duration_formatted', 'N/A')]:
        fields[field] = {'$cond': ['$_is_batch', {'$ifNull': [f'$episodes.{field}', fallback]},
                                  {'$ifNull': [f'${field}', fallback]}]}
    pipeline = [
        {'$match': source},
        {'$addFields': {'_is_batch': {'$isArray': '$episodes'}}},
        {'$unwind': {'path': '$episodes', 'preserveNullAndEmptyArrays': True}},
        {'$project': fields},
        {'$match': {'code': {'$type': 'string', '$ne': ''}}},
    ]
    if query:
        expression = {'$regex': re.escape(query), '$options': 'i'}
        pipeline.append({'$match': {'$or': [{'file_name': expression}, {'batch_title': expression}]}})
    return pipeline


async def list_files(page=1, query='', category='all', section='index'):
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
        result = await collection.aggregate(_pipeline(name, query, section) + [
            {'$sort': {'created_at': -1, 'code': 1}}, {'$facet': facet}]).to_list(length=1)
        result = result[0] if result else {}
        count = result.get('total', [{}])[0].get('value', 0) if result.get('total') else 0
        total += count
        files.extend(result.get('items', []))
        offset = max(0, offset - count)
    return dict(files=files, total=total, page=page, pages=max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE))


async def selected_files(codes, section):
    """Resolve a bounded selection only from its authorized catalog section."""
    found = {}
    for name in CATEGORIES:
        collection = getattr(db, name)
        if collection is None:
            continue
        # Filter documents before unwinding batches; a selection needs no sort.
        # Keep both section checks and the post-unwind match: matching a batch
        # must never include its unselected siblings or private Link records.
        pipeline = [{'$match': {'$or': [
            {'code': {'$in': codes}}, {'_id': {'$in': codes}},
            {'episodes.code': {'$in': codes}},
        ]}}] + _pipeline(name, '', section) + [
            {'$match': {'code': {'$in': codes}}}, {'$limit': PAGE_SIZE}]
        for item in await collection.aggregate(pipeline).to_list(length=PAGE_SIZE):
            found[item['code']] = item
    return [found[code] for code in codes if code in found]
