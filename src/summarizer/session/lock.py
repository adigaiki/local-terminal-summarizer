"""Advisory file locking for multi-process session files.

Uses flock(2) on Linux. Locks are advisory: they coordinate cooperative
processes writing the session notes/state, which is what we are.
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
from typing import BinaryIO

__all__ = ["FileLock"]


class FileLock:
    """Context manager taking an exclusive flock on a lock file."""

    def __init__(self, path: Path, *, timeout: float | None = None) -> None:
        self.path = path
        self.timeout = timeout
        self._handle: BinaryIO | None = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            # The lock file lives next to private session state; keep it
            # restrictive too, even though it holds no data.
            try:
                os.chmod(self.path, 0o600)
            except OSError:  # pragma: no cover - filesystem dependent
                pass
            if self.timeout:
                import time

                deadline = time.monotonic() + self.timeout
                while True:
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError(
                                f"timed out waiting for lock on {self.path}"
                            ) from None
                        time.sleep(0.05)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except BaseException:
            handle.close()
            raise
        self._handle = handle
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._handle is not None:
            try:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            finally:
                self._handle.close()
                self._handle = None
        return False