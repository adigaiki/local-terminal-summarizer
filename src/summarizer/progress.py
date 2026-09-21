"""Human-facing progress on stderr, without breaking Unix composability.

Two rendering modes:

  * **interactive** (stderr is a TTY and progress is enabled): a single
    updating bar drawn in place with ``\\r``; one line per *chunk*, never one
    line per token.
  * **plain** (not a TTY, or explicitly requested): a start line and a final
    line only. This is deliberately quiet so scripts and pipes stay clean.

Progress is *always* written to stderr. stdout remains the program's output
(the summary or JSON) and is never touched here. ``--quiet`` disables all of
it. The reporter holds only counters and timings, so it cannot grow without
bound.
"""

from __future__ import annotations

import sys
import time
from typing import TextIO

from summarizer.log import Diagnostics

__all__ = ["ProgressReporter", "build_progress"]

_FULL = "\u2588"
_EMPTY = "\u2500"
_ASCII_FULL = "#"
_ASCII_EMPTY = "-"


class ProgressReporter:
    """Bounded, stderr-only progress for a long-running operation."""

    def __init__(
        self,
        *,
        diag: Diagnostics,
        enabled: bool = True,
        stream: TextIO | None = None,
        width: int = 24,
    ) -> None:
        self.diag = diag
        self.stream = stream if stream is not None else diag.stream
        self.width = max(8, int(width))
        self.interactive = False
        self.enabled = False
        # Only an interactive terminal gets an updating bar; a non-TTY still
        # gets a start/finish line unless --quiet or config disable it.
        if enabled and not diag.quiet:
            try:
                isatty = bool(self.stream.isatty())
            except Exception:
                isatty = False
            self.interactive = isatty
            self.enabled = True
        self._total = 0
        self._done = 0
        self._label = ""
        self._model = ""
        self._started = 0.0
        self._last_render = 0.0
        self._dirty = False

    # -- lifecycle -----------------------------------------------------------

    def start(self, *, label: str, model: str, total_chunks: int) -> None:
        self._label = label
        self._model = model
        self._total = max(0, int(total_chunks))
        self._done = 0
        self._started = time.monotonic()
        if not self.enabled:
            return
        if self.interactive:
            self._render(force=True)
        else:
            self._write(f"Summarizing {label}\n")

    def update(self, done: int) -> None:
        """Record progress after a chunk completes."""
        self._done = max(self._done, int(done))
        if not self.enabled or not self.interactive:
            return
        now = time.monotonic()
        # Avoid redrawing more than ~10 times/second.
        if now - self._last_render < 0.05 and self._done < self._total:
            self._dirty = True
            return
        self._render()

    def note(self, message: str) -> None:
        """A one-off informative line (resume, cache hit, checkpoints)."""
        if not self.enabled:
            return
        if self.interactive:
            self._clear()
        self._write(message + "\n")
        if self.interactive:
            self._render(force=True)

    def finish(self, *, elapsed: float | None = None) -> None:
        if not self.enabled:
            return
        seconds = time.monotonic() - self._started if elapsed is None else elapsed
        if self.interactive:
            self._clear()
            if self._total > 1:
                self._write(f"{self._label}: {self._done}/{self._total} chunks done\n")
            self._write(f"model: {self._model}\n")
            self._write(f"elapsed: {seconds:.0f}s\n")
        else:
            detail = f" ({self._done}/{self._total} chunks)" if self._total > 1 else ""
            self._write(f"Summarizing {self._label}: done{detail}, {seconds:.0f}s\n")

    # -- internal ------------------------------------------------------------

    def _write(self, text: str) -> None:
        try:
            self.stream.write(text)
            self.stream.flush()
        except (OSError, ValueError):
            # A closed/broken stderr must never break a run.
            self.enabled = False

    def _bar(self) -> str:
        total = max(1, self._total)
        filled = round(self.width * min(self._done, total) / total)
        try:
            return _FULL * filled + _EMPTY * (self.width - filled)
        except UnicodeEncodeError:  # pragma: no cover - exotic terminal
            return _ASCII_FULL * filled + _ASCII_EMPTY * (self.width - filled)

    def _render(self, *, force: bool = False) -> None:
        self._last_render = time.monotonic()
        self._dirty = False
        try:
            bar = self._bar()
        except Exception:
            bar = ""
        elapsed = self._last_render - self._started
        line = f"[{bar}] {self._done}/{self._total} chunks · {self._model} · {elapsed:.0f}s"
        # \r + erase-to-end so the bar updates in place without scrolling.
        self._write("\r\x1b[K" + line)

    def _clear(self) -> None:
        self._write("\r\x1b[K")


def build_progress(
    diag: Diagnostics,
    *,
    enabled: bool = True,
    stream: TextIO | None = None,
) -> ProgressReporter:
    """Create a reporter honoring --quiet and TTY detection."""
    return ProgressReporter(diag=diag, enabled=enabled, stream=stream)


def stderr_is_tty() -> bool:
    try:
        return bool(sys.stderr.isatty())
    except Exception:
        return False
