"""Prepare immutable ZIPs once, then serve seekable files to download managers."""
import asyncio
import logging
import os
import re
import shutil
import time
from contextvars import Context
from contextlib import nullcontext
from pathlib import Path

import aiofiles

from bot.modules.archive import archive_size, stream_archive

logger = logging.getLogger('server')
ARCHIVE_AGE = 24 * 60 * 60


class ArchiveCache:
    # ponytail: one process owns this cache; use shared job leases for replicas.
    def __init__(self, root, max_bytes):
        self.root = Path(root)
        self.max_bytes = max_bytes
        self.jobs = {}
        self.lock = asyncio.Lock()
        self.builder = asyncio.Semaphore(1)

    def path(self, token):
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
            raise ValueError('Invalid download link')
        return self.root / f'{token}.zip'

    def _prune(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        now = time.time()
        for token, job in list(self.jobs.items()):
            if job['expires'] <= now and job['task'].done():
                del self.jobs[token]
        for path in self.root.iterdir():
            if path.suffix == '.zip' and path.stat().st_mtime + ARCHIVE_AGE <= now:
                path.unlink(missing_ok=True)
            elif path.suffix == '.part' and path.stem not in self.jobs:
                path.unlink(missing_ok=True)
        return sum(path.stat().st_size for path in self.root.glob('*.zip'))

    async def start(self, token, files, download, expires, progressive=False):
        self.path(token)
        total = archive_size(files)
        async with self.lock:
            if token in self.jobs:
                return
            if progressive and sum(job['progressive'] and not job['task'].done() for job in self.jobs.values()) >= 2:
                raise ValueError('Live ZIP capacity is full. Try again shortly or choose Prepare with resume.')
            used = await asyncio.to_thread(self._prune)
            reserved = sum(job['total'] for job in self.jobs.values() if not job['task'].done())
            if total + used + reserved > self.max_bytes or len(self.jobs) >= 128:
                raise ValueError('ZIP preparation capacity is full. Select fewer files or try again later.')
            free = (await asyncio.to_thread(shutil.disk_usage, self.root)).free
            if total + reserved + 64 * 1024 * 1024 > free:
                raise ValueError('There is not enough space to prepare this ZIP. Select fewer files.')
            job = dict(state='queued', bytes=0, total=total, expires=expires,
                progressive=progressive, changed=asyncio.Event())
            self.jobs[token] = job
            # A build must outlive the POST and must not retain its request context.
            job['task'] = asyncio.create_task(self._build(token, files, download, job), context=Context())

    async def _build(self, token, files, download, job):
        final = self.path(token)
        partial = final.with_suffix('.part')
        try:
            async with (nullcontext() if job['progressive'] else self.builder):
                if job['expires'] <= time.time():
                    raise RuntimeError('Download link expired during preparation')
                job['state'] = 'preparing'
                async with aiofiles.open(partial, 'wb') as output:
                    os.chmod(partial, 0o600)
                    async for chunk in stream_archive(files, download):
                        if job['expires'] <= time.time():
                            raise RuntimeError('Download link expired during preparation')
                        if job['bytes'] + len(chunk) > job['total']:
                            raise RuntimeError('ZIP source changed during preparation')
                        await output.write(chunk)
                        if job['progressive']:
                            # Publish only flushed bytes; readers must never see buffered gaps.
                            await output.flush()
                        job['bytes'] += len(chunk)
                        self._notify(job)
                if job['bytes'] != job['total']:
                    raise RuntimeError('ZIP length changed during preparation')
                stamp = job['expires'] - ARCHIVE_AGE
                os.utime(partial, (stamp, stamp))
                partial.replace(final)
                job['state'] = 'ready'
        except asyncio.CancelledError:
            job['state'] = 'failed'
            raise
        except Exception as error:
            job['state'] = 'failed'
            logger.warning('ZIP preparation failed (%s).', type(error).__name__)
        finally:
            partial.unlink(missing_ok=True)
            self._notify(job)

    @staticmethod
    def _notify(job):
        changed = job['changed']
        job['changed'] = asyncio.Event()
        changed.set()

    async def read(self, token, start, stop):
        """Read a byte range from the shared growing ZIP, waiting for missing bytes."""
        final = self.path(token)
        source = None
        try:
            while start < stop:
                job = self.jobs.get(token)
                if job is None:
                    available = final.stat().st_size
                    state, changed = 'ready', None
                else:
                    available, state, changed = job['bytes'], job['state'], job['changed']
                if available > start:
                    if source is None:
                        try:
                            source = await aiofiles.open(final.with_suffix('.part'), 'rb')
                        except FileNotFoundError:
                            source = await aiofiles.open(final, 'rb')
                        await source.seek(start)
                    chunk = await source.read(min(1024 * 1024, available - start, stop - start))
                    if not chunk:
                        raise RuntimeError('ZIP cache ended before its advertised bytes')
                    start += len(chunk)
                    yield chunk
                elif state in ('queued', 'preparing'):
                    await changed.wait()
                else:
                    raise RuntimeError('ZIP preparation stopped before this range was available')
        finally:
            if source is not None:
                await source.close()

    def status(self, token, total):
        path = self.path(token)
        if path.is_file() and path.stat().st_size == total:
            return dict(state='ready', bytes=total, total=total)
        job = self.jobs.get(token)
        if job and job['state'] != 'ready':
            return {key: job[key] for key in ('state', 'bytes', 'total')}
        return dict(state='missing', bytes=0, total=total)

    async def close(self):
        tasks = [job['task'] for job in self.jobs.values() if not job['task'].done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


archive_cache = ArchiveCache(
    os.environ.get('ZIP_CACHE_DIR') or Path(__file__).resolve().parents[2] / 'cache' / 'archives',
    max(1, int(os.environ.get('ZIP_CACHE_MAX_MB', '8192'))) * 1024 * 1024,
)
