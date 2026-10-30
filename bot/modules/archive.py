"""Stream stored ZIP64 archives with bounded memory and no temporary media files."""
import io
import re
from collections import deque
from zipfile import ZipFile, ZIP64_LIMIT, ZIP_FILECOUNT_LIMIT


class _Sink(io.RawIOBase):
    def __init__(self):
        super().__init__()
        self.chunks = deque()
        self.position = 0

    def tell(self):
        return self.position

    def write(self, data):
        if data:
            self.chunks.append(data)
            self.position += len(data)
        return len(data)


def archive_name(number, item):
    name = re.sub(r'[\x00-\x1f\x7f/\\:<>"|?*]', '_', item.get('file_name') or 'file')
    return f'{number:02d}-{name[:180].rstrip(". ") or "file"}'


def archive_size(files):
    """Exact length for this stored, descriptor-based ZIP64 layout."""
    offset, central = 0, 0
    for number, item in enumerate(files, 1):
        name = archive_name(number, item)
        try:
            encoded = name.encode('ascii')
        except UnicodeEncodeError:
            encoded = name.encode('utf-8')
        size = int(item['file_size'])
        if size < 0:
            raise ValueError('Invalid file size')
        extra = (16 if size > ZIP64_LIMIT else 0) + (8 if offset > ZIP64_LIMIT else 0)
        central += 46 + len(encoded) + (4 + extra if extra else 0)
        offset += 30 + len(encoded) + 20 + size + 24
    zip64 = offset > ZIP64_LIMIT or central > ZIP64_LIMIT or len(files) > ZIP_FILECOUNT_LIMIT
    return offset + central + 22 + (76 if zip64 else 0)


async def stream_archive(files, download):
    sink = _Sink()
    with ZipFile(sink, 'w') as archive:
        for number, item in enumerate(files, 1):
            name = archive_name(number, item)
            response = await download(item['code'])
            if response.status_code != 200:
                raise RuntimeError('A selected file could not be downloaded')
            expected = int(response.headers['Content-Length'])
            if 'file_size' in item and expected != int(item['file_size']):
                raise RuntimeError('Selected file size changed during preparation')
            received = 0
            with archive.open(name, 'w', force_zip64=True) as entry:
                while sink.chunks:
                    yield sink.chunks.popleft()
                async with response.response as body:
                    async for chunk in body:
                        received += len(chunk)
                        if received > expected:
                            raise RuntimeError('Selected file size changed during transfer')
                        entry.write(chunk)
                        while sink.chunks:
                            yield sink.chunks.popleft()
                if received != expected:
                    raise RuntimeError('A selected file transfer was incomplete')
            while sink.chunks:
                yield sink.chunks.popleft()
    while sink.chunks:
        yield sink.chunks.popleft()


def archive_filename(value, section='index'):
    """A display name only; cache paths always use the random ticket token."""
    import unicodedata
    name = unicodedata.normalize('NFC', value or '').strip()
    if len(name) > 120 or len(name.encode('utf-8')) > 240:
        raise ValueError('That ZIP name is too long. Try a shorter name, without the extension.')
    name = ''.join(c for c in name if not unicodedata.category(c).startswith('C'))
    name = re.sub(r'[<>:"/\\|?*]', '_', name).strip(' .')
    while name.lower().endswith('.zip'):
        name = name[:-4].rstrip(' .')
    if not name:
        name = f'GateStream-{section}'
    if name.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1,10)), *(f'LPT{i}' for i in range(1,10))}:
        name = '_' + name
    return name + '.zip'


def archive_disposition(name):
    from urllib.parse import quote
    from werkzeug.datastructures import Headers
    from werkzeug.utils import secure_filename
    headers = Headers()
    headers.set('Content-Disposition', 'attachment', filename=name if name.isascii() else (secure_filename(name) or 'download.zip'),
                **{'filename*': "UTF-8''" + quote(name, safe='')})
    return headers['Content-Disposition']
