"""Read bounded, limited input from stdin without slurping unlimited data.

Every read path resolves its limits through
:func:`summarizer.document.reader.effective_limit`, so a missing or
non-positive limit becomes a finite absolute ceiling rather than "read until
EOF". An effectively infinite stdin stream therefore cannot be pulled into
memory.
"""

from __future__ import annotations

import sys
from typing import BinaryIO

from summarizer.document.model import Document
from summarizer.document.reader import (
    ABSOLUTE_MAX_BYTES,
    ABSOLUTE_MAX_LINES,
    decode_bytes,
    effective_limit,
)
from summarizer.errors import InputError
from summarizer.log import Diagnostics

__all__ = ["read_stdin", "bounded_read", "READ_BLOCK_BYTES"]

# Read granularity: small enough to abort early, large enough to stay fast.
READ_BLOCK_BYTES = 256 * 1024
_READ_CHUNK = READ_BLOCK_BYTES


def bounded_read(
    raw: BinaryIO,
    *,
    max_bytes: int,
    max_lines: int,
    what: str = "input",
) -> bytes:
    """Read at most max_bytes bytes / max_lines lines from a binary stream.

    Raises :class:`InputError` when a limit is exceeded instead of silently
    truncating, so the caller never summarizes a document that was quietly cut
    in half. Limits are always finite (see ``effective_limit``).
    """
    limit_bytes = effective_limit(max_bytes, ABSOLUTE_MAX_BYTES)
    limit_lines = effective_limit(max_lines, ABSOLUTE_MAX_LINES)

    chunks: list[bytes] = []
    total = 0
    lines = 0
    while True:
        chunk = raw.read(_READ_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        lines += chunk.count(b"\n")
        # Check before keeping the block: an oversized stream must not grow
        # the buffer any further.
        if total > limit_bytes:
            raise InputError(
                f"{what} exceeds maximum of {limit_bytes} bytes "
                f"(see `input.max_bytes` in config)",
                hint="raise the limit or feed a smaller document",
            )
        if lines + (not chunk.endswith(b"\n")) > limit_lines:
            raise InputError(
                f"{what} exceeds maximum of {limit_lines} lines "
                f"(see `input.max_lines` in config)",
                hint="raise the limit or feed a shorter document",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def read_stdin(
    *,
    diag: Diagnostics,
    options: dict | None = None,
    raw: BinaryIO | None = None,
) -> Document:
    """Read standard input into a :class:`Document` with hard limits."""
    options = options or {}
    max_bytes = effective_limit(options.get("max_bytes"), ABSOLUTE_MAX_BYTES)
    max_lines = effective_limit(options.get("max_lines"), ABSOLUTE_MAX_LINES)
    encoding = options.get("encoding") or "utf-8"
    raw = raw if raw is not None else sys.stdin.buffer

    data = bounded_read(raw, max_bytes=max_bytes, max_lines=max_lines, what="stdin")
    content = decode_bytes(data, encoding, diag=diag, what="stdin input")
    return Document(
        content=content,
        source="stdin",
        mime_type="text/plain",
        encoding=encoding,
        size=len(data),
        metadata={"stream": True},
    )
