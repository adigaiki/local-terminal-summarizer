"""Session records: compact pointers, never full summaries by default."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from summarizer.log import Diagnostics

__all__ = ["one_sentence_extract", "append_note", "NoteRecord"]

_SENTENCE_ENDINGS = (". ", "! ", "? ", ".\n", "!\n", "?\n")


def one_sentence_extract(summary: str, max_chars: int = 200) -> str:
    """First sentence of a summary, truncated — a compact pointer."""
    text = " ".join(summary.split())
    cut = len(text)
    for ending in _SENTENCE_ENDINGS:
        pos = text.find(ending)
        if pos != -1:
            cut = min(cut, pos + 1)
    return text[: min(cut, max_chars)]


def append_note(
    notes_dir: Path,
    note: dict[str, Any],
    *,
    digest_format: str = "markdown",
    append_mode: bool = True,
    diag: Diagnostics | None = None,
) -> Path:
    """Append one session note atomically with restrictive permissions."""
    notes_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".jsonl" if digest_format == "json" else ".md"
    path = notes_dir / f"digest{suffix}"

    from summarizer.output.atomic import atomic_write

    mode = "a" if append_mode else "w"
    with atomic_write(path, mode=mode) as handle:
        if digest_format == "json":
            handle.write(json.dumps(note, ensure_ascii=False) + "\n")
        else:
            handle.write(
                "- **{time}** source=`{source}` profile=`{profile}`\n"
                "    {extract}\n".format(
                    time=note.get("time", ""),
                    source=note.get("source", "?"),
                    profile=note.get("profile", "?"),
                    extract=note.get("extract", ""),
                )
            )
    try:
        path.chmod(0o600)
    except OSError as exc:
        if diag:
            diag.verbose_warn(f"could not restrict permissions on {path}: {exc}")
    return path


def make_note(*, source: str, profile: str, summary: str | None) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {
        "time": now,
        "source": source,
        "profile": profile,
        "extract": one_sentence_extract(summary or ""),
    }