"""One project-specific supervisor, with no pgrep or unbounded output files.

Run periodically only where your hosting provider permits persistent processes.
The supervisor stays with its child (one extra process); concurrent invocations exit.
"""
import fcntl
import os
from pathlib import Path
import signal
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    os.umask(0o077)
    with (root / '.watchdog.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        child = subprocess.Popen([sys.executable, '-m', 'bot'], cwd=root,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        def forward(signum, _frame):
            if child.poll() is None:
                child.send_signal(signum)
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, forward)
        return child.wait()


if __name__ == '__main__':
    raise SystemExit(main())
