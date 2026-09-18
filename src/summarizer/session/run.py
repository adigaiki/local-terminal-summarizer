"""The small run event a session consumes, plus the recording policy.

The session subsystem stays independent of readers, engines, chunking and
model logic. A finished run is adapted through attribute access only, and the
stored record is pointer-sized: no document text, no prompt text, no full
model output.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from summarizer.errors import InputError
from summarizer.log import Diagnostics
from summarizer.session.digest import one_sentence_extract
from summarizer.session.manager import SessionInfo, SessionManager

__all__ = ["SessionRun", "ENV_VAR", "select_session", "record_selected"]

#: Environment variable that explicitly selects a session for this shell.
ENV_VAR = "SUMMARIZER_SESSION"


def _extract_from_summary(summary: Any) -> str:
    """A one-sentence pointer, whatever shape the model returned."""
    if isinstance(summary, str):
        return one_sentence_extract(summary)
    if isinstance(summary, dict):
        for value in summary.values():
            if isinstance(value, str) and value.strip():
                return one_sentence_extract(value)
    return ""


@dataclass(frozen=True)
class SessionRun:
    """One compact pointer to a completed run."""

    source: str
    profile: str
    extract: str
    time: str
    document_type: str = "text/plain"
    pages: int | None = None
    chunks: int = 1
    strategy: str = "direct"
    output_format: str = "markdown"
    duration_seconds: float | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "time": self.time,
            "source": self.source,
            "profile": self.profile,
            "document_type": self.document_type,
            "pages": self.pages,
            "chunks": self.chunks,
            "strategy": self.strategy,
            "output_format": self.output_format,
            "duration_seconds": self.duration_seconds,
            "extract": self.extract,
        }

    @classmethod
    def from_result(cls, result: Any, *, when: str | None = None) -> "SessionRun":
        """Adapt a finished pipeline result without importing the pipeline."""
        document = getattr(result, "document", None)
        metadata = getattr(document, "metadata", None) or {}
        chunks = getattr(result, "chunks", None) or []
        aggregation = getattr(result, "aggregation", None)
        profile = getattr(result, "profile", None)
        duration = getattr(result, "duration", 0.0) or 0.0
        pages = metadata.get("pages")
        return cls(
            source=str(getattr(document, "source", "?")),
            profile=str(getattr(profile, "name", "?")),
            extract=_extract_from_summary(getattr(result, "summary", None)),
            time=when or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            document_type=str(getattr(document, "mime_type", "text/plain")),
            pages=pages if isinstance(pages, int) and pages > 0 else None,
            chunks=len(chunks) if chunks else 1,
            strategy=str(getattr(aggregation, "name", "direct")),
            output_format=str(getattr(result, "output_format", "markdown")),
            duration_seconds=round(float(duration), 3) if duration else None,
        )


def select_session(
    *,
    root: Path,
    max_age_hours: float,
    digest_format: str,
    enabled: bool,
    explicit: str | None = None,
    no_session: bool = False,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    diag: Diagnostics,
) -> SessionInfo | None:
    """Resolve the selected session *before* the run, or ``None`` for no session.

    Explicit selections (`--session`, `$SUMMARIZER_SESSION`) raise a clear
    error here, before any model work happens, so a typo never costs a run.
    """
    if no_session or not enabled:
        return None
    manager = SessionManager(
        root=root, diag=diag, max_age_hours=max_age_hours, digest_format=digest_format
    )
    environment = os.environ if env is None else env
    return manager.select(
        explicit=explicit,
        env_name=(environment.get(ENV_VAR) or None),
        cwd=cwd if cwd is not None else Path.cwd(),
    )


def record_selected(
    *, session: SessionInfo, result: Any, digest_format: str, diag: Diagnostics
) -> Path | None:
    """Store one pointer for a finished run. Never fatal to summarization."""
    try:
        manager = SessionManager(
            root=session.directory.parent, diag=diag, digest_format=digest_format
        )
        return manager.record(session.name, SessionRun.from_result(result))
    except InputError as exc:
        diag.warn(f"session not updated: {exc.message}")
    except Exception as exc:  # never let session bookkeeping break a run
        diag.warn(f"could not record session run: {exc}")
    return None
