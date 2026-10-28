"""Stream stored ZIP64 archives with bounded memory and no temporary media files."""
import io
import re
from collections import deque
from zipfile import ZipFile


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


async def stream_archive(files, download):
    sink = _Sink()
    with ZipFile(sink, 'w') as archive:
        for number, item in enumerate(files, 1):
            # Flat unique names prevent path traversal, overwrite and extraction issues.
            name = re.sub(r'[\x00-\x1f\x7f/\\:<>"|?*]', '_', item.get('file_name') or 'file')
            name = f'{number:02d}-{name[:180].rstrip(". ") or "file"}'
            response = await download(item['code'])
            if response.status_code != 200:
                raise RuntimeError('A selected file could not be downloaded')
            expected = int(response.headers['Content-Length'])
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
