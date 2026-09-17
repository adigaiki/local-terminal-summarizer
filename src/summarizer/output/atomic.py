"""Atomic file writes: never leave a corrupt partially-written output file."""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, TextIO

from summarizer.errors import InputError

__all__ = ["atomic_write"]


@contextmanager
def atomic_write(path: str | Path, *, mode: str = "w", encoding: str = "utf-8") -> Iterator[TextIO]:
    """Write to `path` atomically.

    The target is never touched until the body succeeds; on success the
    temporary file is renamed over the target. On any exception (including
    KeyboardInterrupt) the temporary file is removed and the previous target
    is left intact.
    """
    target = Path(path)
    parent = target.parent if str(target.parent) else Path(".")
    if target.exists() and target.is_dir():
        raise InputError(f"output path {target} is a directory")
    if not parent.is_dir():
        raise InputError(
            f"output directory does not exist: {parent}",
            hint="create the directory first, then retry",
        )

    try:
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=str(parent),
        )
    except OSError as exc:
        raise InputError(f"cannot create temporary output next to {target}: {exc}") from exc
    tmp_path = Path(tmp_name)
    os.close(fd)
    try:
        with tmp_path.open(mode, encoding=encoding, newline="") as handle:
            yield handle
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
