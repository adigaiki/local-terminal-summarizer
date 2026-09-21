"""Cooperative cancellation for a single run.

Cancellation is a first-class pipeline behavior, not just an exception that
happens to propagate. A :class:`CancelToken` is created per run, flipped by
the SIGINT handler (and by tests), and checked at stage boundaries and inside
long-running loops (map workers, streaming reads, output).

The token is deliberately tiny and thread-safe: map workers run in threads,
and they must be able to observe a main-thread cancellation.
"""

from __future__ import annotations

import threading

from summarizer.errors import Interrupted

__all__ = ["CancelToken"]


class CancelToken:
    """A one-way, thread-safe cancellation flag."""

    __slots__ = ("_event",)

    def __init__(self, *, cancelled: bool = False) -> None:
        self._event = threading.Event()
        if cancelled:
            self._event.set()

    def cancel(self) -> None:
        """Request cancellation. Idempotent and safe from any thread."""
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        """Block until cancelled (or timeout); returns the cancelled state."""
        return self._event.wait(timeout)

    def reset_for_tests(self) -> None:  # pragma: no cover - test convenience
        self._event.clear()

    def raise_if_cancelled(self, message: str = "interrupted") -> None:
        """Raise :class:`Interrupted` when cancellation was requested."""
        if self._event.is_set():
            raise Interrupted(message)
