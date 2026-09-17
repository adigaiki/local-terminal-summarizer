"""Read plain text files with encoding fallback and hard limits."""

from __future__ import annotations

from pathlib import Path

from summarizer.document.metadata import file_metadata
from summarizer.document.model import Document
from summarizer.document.reader import (
    ABSOLUTE_MAX_BYTES,
    ABSOLUTE_MAX_LINES,
    checked_size, decode_text, effective_limit,
)
from summarizer.errors import InputError
from summarizer.log import Diagnostics
from summarizer.readers.stdin import bounded_read

__all__ = ["read_text_file"]


def read_text_file(path: Path, *, diag: Diagnostics, options: dict | None = None) -> Document:
    """Read a text file, never reading more than the configured limits."""
    options = options or {}
    max_bytes = effective_limit(options.get("max_bytes"), ABSOLUTE_MAX_BYTES)
    max_lines = effective_limit(options.get("max_lines"), ABSOLUTE_MAX_LINES)
    encoding = options.get("encoding") or "utf-8"

    # Fail on the declared size before opening the file at all, then stream the
    # content through the same bounded reader used for stdin so a file that
    # grew between stat and read still cannot blow up memory.
    checked_size(path, max_bytes)
    try:
        with path.open("rb") as handle:
            raw = bounded_read(handle, max_bytes=max_bytes, max_lines=max_lines, what=str(path))
    except OSError as exc:
        raise InputError(f"cannot read {path}: {exc}") from exc

    content, encoding_used, warnings = decode_text(raw, encoding, diag=diag, what=f"{path}")
    metadata = file_metadata(path)
    if warnings:
        metadata["warnings"] = warnings
    return Document(
        content=content,
        source=str(path),
        mime_type="text/plain",
        encoding=encoding_used,
        size=len(raw),
        metadata=metadata,
    )
