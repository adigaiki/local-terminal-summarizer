"""Streaming output cleanup and stdout discipline.

stdout is for program output only; all diagnostics go through Diagnostics
(stderr). This module owns the "emit tokens as they arrive" behavior and the
Ctrl-C handling that goes with it.
"""

from __future__ import annotations

import sys
from typing import Iterator, TextIO

from summarizer.errors import Interrupted

__all__ = ["stream_write", "stream_to_io"]

# How often to flush while streaming (per token flush is simplest and keeps
# pipes flowing; token count is small).
STREAM_FLUSH_EVERY = 1


def stream_to_io(stream: Iterator[str], io: TextIO = sys.stdout) -> None:
    """Write streamed deltas to `io`, flushing as output arrives.

    If the user interrupts (Ctrl-C), the stream generator raises
    Interrupted; partial output is left in place for stdout (it is already
    visible), matching the behavior of tools like `git`.
    """
    try:
        for delta in stream:
            if not delta:
                continue
            io.write(delta)
            io.flush()
    except KeyboardInterrupt as exc:
        raise Interrupted("interrupted") from exc


def stream_write(stream: Iterator[str], io: TextIO) -> None:
    """Alias used for symmetry in tests."""
    stream_to_io(stream, io)