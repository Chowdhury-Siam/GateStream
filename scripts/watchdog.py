"""Start one GateStream process while holding the project lock."""
import fcntl
import os
from pathlib import Path
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    os.umask(0o077)
    devnull = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):
        os.dup2(devnull, fd)
    if devnull > 2:
        os.close(devnull)

    with (root / '.watchdog.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0

        os.chdir(root)
        # The bot itself owns the lock, including across its graceful exec restarts.
        os.set_inheritable(lock.fileno(), True)
        os.execv(sys.executable, [sys.executable, '-m', 'bot'])


if __name__ == '__main__':
    raise SystemExit(main())
