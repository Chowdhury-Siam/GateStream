"""Thread-safe activity counters with restart-safe cumulative checkpoints."""
import asyncio
import time
from collections import deque
from contextlib import contextmanager
from threading import Lock


class TransferActivity:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.started = clock()
        self.lock = Lock()
        self.serial = 0
        self.jobs = {}
        self.totals = {kind: dict(bytes=0, completed=0, failed=0, incomplete=0)
                       for kind in ('media', 'download', 'upload')}
        self.traffic = {kind: deque() for kind in self.totals}
        self.restored = False
        self.persistence = dict(state='pending', last_saved_at=None)

    def restore(self, phases):
        """Add saved totals once; jobs/rates from an old process cannot stay active."""
        if not isinstance(phases, dict):
            raise ValueError('Invalid activity checkpoint')
        with self.lock:
            if self.restored:
                return
            historical = {}
            for kind, totals in self.totals.items():
                saved = phases.get(kind, {})
                historical[kind] = {key: max(0, int(saved.get(key, 0))) for key in totals}
                historical[kind]['incomplete'] += max(0, int(saved.get('active', 0)))
            for kind, values in historical.items():
                for key, value in values.items():
                    self.totals[kind][key] += value
            self.restored = True

    def checkpoint(self):
        with self.lock:
            return {kind: {**totals, 'active': sum(job['kind'] == kind for job in self.jobs.values())}
                    for kind, totals in self.totals.items()}

    def saved_state(self, state, timestamp=None):
        with self.lock:
            self.persistence['state'] = state
            if timestamp is not None:
                self.persistence['last_saved_at'] = timestamp

    def start(self, kind, total=None, key=None):
        with self.lock:
            if kind not in self.totals:
                raise ValueError('Unknown transfer kind')
            self.serial += 1
            self.jobs[self.serial] = dict(kind=kind, total=total, key=key, done=0, started=self.clock())
            return self.serial

    def progress(self, ident, done, total=None):
        with self.lock:
            job = self.jobs.get(ident)
            if job is None:
                return
            done = max(job['done'], int(done))
            delta = done - job['done']
            job['done'] = done
            if total is not None:
                job['total'] = max(0, int(total))
            self.totals[job['kind']]['bytes'] += delta
            if delta:
                second = int(self.clock())
                traffic = self.traffic[job['kind']]
                if traffic and traffic[-1][0] == second:
                    traffic[-1] = (second, traffic[-1][1] + delta)
                else:
                    traffic.append((second, delta))
                while traffic and traffic[0][0] < second - 5:
                    traffic.popleft()

    def finish(self, ident, outcome='completed'):
        with self.lock:
            job = self.jobs.pop(ident, None)
            if job:
                self.totals[job['kind']][outcome] += 1

    @contextmanager
    def transfer(self, kind, total=None):
        ident = self.start(kind, total)
        outcome = 'completed'
        try:
            yield ident
        except BaseException as error:
            outcome = 'incomplete' if isinstance(error, (asyncio.CancelledError, GeneratorExit)) else 'failed'
            raise
        finally:
            self.finish(ident, outcome)

    async def track_stream(self, stream, total, key):
        ident = self.start('media', total, key)
        done = 0
        outcome = 'incomplete'
        try:
            async for chunk in stream:
                done += len(chunk)
                self.progress(ident, done)
                yield chunk
            if done >= total:
                outcome = 'completed'
        except Exception:
            outcome = 'failed'
            raise
        finally:
            try:
                await stream.aclose()
            finally:
                self.finish(ident, outcome)

    def snapshot(self):
        now = self.clock()
        with self.lock:
            phases = {}
            for kind, totals in self.totals.items():
                traffic = self.traffic[kind]
                while traffic and traffic[0][0] < now - 5:
                    traffic.popleft()
                phases[kind] = {**totals,
                    'active': sum(job['kind'] == kind for job in self.jobs.values()),
                    'speed_bps': sum(size for _, size in traffic) / min(5, max(1, now - self.started))}
            transfers = []
            for ident, job in list(self.jobs.items())[-20:][::-1]:
                elapsed = max(0, now - job['started'])
                speed = job['done'] / elapsed if elapsed > 0 else 0
                total = job['total']
                transfers.append(dict(id=ident, kind=job['kind'], bytes=job['done'],
                    total_bytes=total, elapsed_seconds=elapsed, speed_bps=speed,
                    percent=min(100, 100 * job['done'] / total) if total else None,
                    eta_seconds=max(0, (total - job['done']) / speed) if total is not None and speed > 0 else None))
            return dict(uptime_seconds=max(0, now - self.started), phases=phases, persistence=dict(self.persistence),
                active_media_links=len({job['key'] for job in self.jobs.values() if job['kind'] == 'media'}),
                transfers=transfers, hidden_transfers=max(0, len(self.jobs) - len(transfers)))


activity = TransferActivity()
