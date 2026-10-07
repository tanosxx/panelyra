"""One stream per user, with PID-reuse-safe status and graceful stop."""

import fcntl
from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import stat


@contextmanager
def cancel_on_term():
    """Unwind connection setup on GUI Stop, including owned ADB forwards."""
    def interrupted(*_):
        raise KeyboardInterrupt
    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def runtime_path():
    base = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/panelyra-{os.getuid()}")
    folder = base / "panelyra"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = folder.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError("Panelyra runtime directory must be private and owned by the current user")
    return folder / "stream.lock"


def process_start(pid):
    try:
        proc = Path("/proc") / str(pid)
        if proc.stat().st_uid != os.getuid():
            return None
        return (proc / "stat").read_text().rsplit(") ", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def _open(path):
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        os.close(fd)
        raise RuntimeError("Unsafe Panelyra state file")
    return fd


class Instance:
    def __init__(self, mode=None, path=None):
        self.path = Path(path) if path is not None else runtime_path()
        self.fd = None
        self.mode = mode or {}

    def __enter__(self):
        self.fd = _open(self.path)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self.fd)
            self.fd = None
            raise RuntimeError("Panelyra is already running. Use 'panelyra status' or 'panelyra stop'.") from None
        payload = {**self.mode, "pid": os.getpid(), "start": process_start(os.getpid())}
        try:
            os.ftruncate(self.fd, 0)
            os.write(self.fd, json.dumps(payload).encode())
        except BaseException:
            os.close(self.fd)
            self.fd = None
            raise
        return self

    def __exit__(self, *_):
        if self.fd is not None:
            # Keep the same inode: unlinking a lock permits races with waiters.
            os.ftruncate(self.fd, 0)
            os.close(self.fd)
            self.fd = None


def status(path=None):
    path = Path(path) if path is not None else runtime_path()
    fd = _open(path)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return {"running": False}
        except BlockingIOError:
            pass
        try:
            record = json.loads(os.read(fd, 8192))
            pid = record["pid"]
            if type(pid) is not int or not 1 < pid <= 2**31 - 1:
                return {"running": False}
            actual_start = process_start(pid)
            if actual_start is None or actual_start != record.get("start"):
                return {"running": False}
            return {**record, "running": True}
        except (ValueError, KeyError, TypeError):
            return {"running": True, "starting": True}
    finally:
        os.close(fd)


def stop(path=None):
    record = status(path)
    if not record.get("running"):
        return False
    if "pid" not in record:
        raise RuntimeError("Panelyra is still starting; retry in a moment")
    pid = record["pid"]
    # pidfd keeps a rapidly exiting PID from accidentally referring to another
    # process between the identity check and the signal.
    try:
        fd = os.pidfd_open(pid)
    except ProcessLookupError:
        return False
    try:
        if process_start(pid) != record["start"]:
            return False
        signal.pidfd_send_signal(fd, signal.SIGTERM)
        return True
    except ProcessLookupError:
        return False
    finally:
        os.close(fd)
