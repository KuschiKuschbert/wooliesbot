"""Runtime lock helpers extracted from chef_os.py."""

from __future__ import annotations

import fcntl
import os


def acquire_file_lock(lock_path: str, warning_logger):
    """Return fd if exclusive lock acquired, else None."""
    try:
        os.makedirs(os.path.dirname(lock_path), exist_ok=True)
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    except OSError as exc:
        warning_logger(f"Could not open lock file {lock_path}: {exc}")
        return None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BlockingIOError:
        try:
            os.close(fd)
        except OSError:
            pass
        return None


def release_file_lock(fd):
    if fd is None:
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        os.close(fd)
    except OSError:
        pass
