"""Diagnostics: all human-facing progress/status output goes to stderr.

stdout is reserved for program output (the summary, strict JSON bodies,
explicit query results). Nothing in this module ever writes to stdout.
"""

from __future__ import annotations

import sys
from typing import TextIO

__all__ = ["Diagnostics"]


class Diagnostics:
    """A small stderr logger with verbosity control.

    Quiet mode still shows hard errors. Verbose mode adds detail.
    Debug mode appends tracebacks. Warnings are also captured so the
    pipeline can include them in structured (JSON) output.
    """

    def __init__(
        self,
        *,
        quiet: bool = False,
        verbose: bool = False,
        debug: bool = False,
        stream: TextIO | None = None,
        program_name: str = "summarize",
    ) -> None:
        self.quiet = quiet
        self.verbose = verbose or debug
        self.debug = debug
        self.stream = stream if stream is not None else sys.stderr
        self.program_name = program_name
        self.warnings: list[str] = []

    def _emit(self, level: str, message: str) -> None:
        self.stream.write(f"{self.program_name}: {level}: {message}\n")
        self.stream.flush()

    def info(self, message: str) -> None:
        if not self.quiet:
            self.stream.write(f"{message}\n")
            self.stream.flush()

    def progress(self, message: str) -> None:
        """Progress/diagnostic line shown unless --quiet."""
        if not self.quiet:
            self.info(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)
        if not self.quiet:
            self._emit("warning", message)

    def error(self, message: str) -> None:
        # Errors always show, even with --quiet.
        self._emit("error", message)

    def verbose_message(self, message: str) -> None:
        if self.verbose and not self.quiet:
            self.stream.write(f"{message}\n")
            self.stream.flush()

    def verbose_warn(self, message: str) -> None:
        if self.verbose and not self.quiet:
            self._emit("warning", message)

    def debug_message(self, message: str) -> None:
        if self.debug:
            self.stream.write(f"{self.program_name}: debug: {message}\n")
            self.stream.flush()

    def take_warnings(self) -> list[str]:
        captured = list(self.warnings)
        self.warnings = []
        return captured


def redact_url(url: str) -> str:
    """Strip any userinfo credentials from a URL before printing it."""
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(url)
    if parts.username is None and parts.password is None:
        return url
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))