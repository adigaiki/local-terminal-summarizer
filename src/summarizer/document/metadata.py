"""Metadata helpers shared by readers.

Provenance fields (filename, line ranges, pages, headings) are recorded here
so readers stay thin and the document model stays stable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

__all__ = ["file_metadata", "merge_metadata"]


def file_metadata(path: Path) -> dict[str, Any]:
    """Basic provenance for filesystem sources."""
    meta: dict[str, Any] = {"filename": path.name}
    try:
        stat = path.stat()
        meta["size_bytes"] = stat.st_size
        meta["mtime"] = int(stat.st_mtime)
    except OSError:
        pass
    return meta


def merge_metadata(*items: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in items:
        if item:
            out.update(item)
    return out