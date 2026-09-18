"""Session digests: compact pointers, never full summaries.

The digest is *rendered from session state*, so it can never be left half
written or inconsistent with the machine-readable record: every update writes
a complete document atomically. Nothing here copies model output or document
text -- a run contributes only a short extract and pointer-sized metadata.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from summarizer.log import Diagnostics
from summarizer.output.atomic import atomic_write

__all__ = [
    "one_sentence_extract",
    "render_digest_markdown",
    "render_digest_json",
    "write_digest",
    "DIGEST_MARKDOWN",
    "DIGEST_JSON",
]

_SENTENCE_ENDINGS = (". ", "! ", "? ", ".\n", "!\n", "?\n")

DIGEST_MARKDOWN = "digest.md"
DIGEST_JSON = "digest.json"

_MAX_EXTRACT_CHARS = 200


def one_sentence_extract(summary: str, max_chars: int = _MAX_EXTRACT_CHARS) -> str:
    """First sentence of a summary, truncated -- a compact pointer."""
    text = " ".join(summary.split())
    cut = len(text)
    for ending in _SENTENCE_ENDINGS:
        pos = text.find(ending)
        if pos != -1:
            cut = min(cut, pos + 1)
    return text[: min(cut, max_chars)]


def _local_hhmm(timestamp: str) -> str:
    """Render a stored UTC timestamp as local HH:MM for human reading."""
    try:
        moment = datetime.fromisoformat(timestamp)
    except (TypeError, ValueError):
        return "??:??"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone().strftime("%H:%M")


def _row(run: dict[str, Any]) -> str:
    source = str(run.get("source", "?"))
    profile = str(run.get("profile", "?"))
    extract = str(run.get("extract", "")).replace("\n", " ").strip()
    extras: list[str] = []
    pages = run.get("pages")
    if isinstance(pages, int) and pages > 0:
        extras.append(f"{pages}p")
    chunks = run.get("chunks")
    if isinstance(chunks, int) and chunks > 1:
        extras.append(f"{chunks} chunks")
    suffix = f" [{', '.join(extras)}]" if extras else ""
    shown = f'"{extract}"' if extract else ""
    return (
        f"| {_local_hhmm(str(run.get('time', '')))}"
        f" | `{source}` | {profile}{suffix} | {shown} |"
    )


def render_digest_markdown(state: dict[str, Any]) -> str:
    """Human-readable digest built entirely from compact run pointers."""
    run_list = [run for run in state.get("runs", []) if isinstance(run, dict)]
    lines = [
        f"# Session {state.get('name', '?')}",
        "",
        f"- id: `{state.get('id', '?')}`",
        f"- state: {state.get('state', '?')}",
        f"- created: {state.get('created', '?')}",
        f"- last activity: {state.get('last_activity', '?')}",
        f"- runs: {len(run_list)}",
        "",
        "Pointers only: no full summaries and no document text are stored.",
        "",
    ]
    if not run_list:
        lines.append("_No runs recorded yet._")
        return "\n".join(lines) + "\n"
    lines += ["| time | source | profile | extract |", "| --- | --- | --- | --- |"]
    lines += [_row(run) for run in run_list]
    return "\n".join(lines) + "\n"


def render_digest_json(state: dict[str, Any]) -> str:
    """Machine-readable digest (separate from the summary JSON schema)."""
    payload = {
        "schema": state.get("schema"),
        "name": state.get("name"),
        "id": state.get("id"),
        "state": state.get("state"),
        "created": state.get("created"),
        "last_activity": state.get("last_activity"),
        "closed_at": state.get("closed_at"),
        "runs": [run for run in state.get("runs", []) if isinstance(run, dict)],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def write_digest(
    directory: Path,
    state: dict[str, Any],
    *,
    digest_format: str = "markdown",
    diag: Diagnostics | None = None,
) -> list[Path]:
    """Atomically (re)write the digest files; returns the paths written.

    ``digest_format`` is ``markdown``, ``json``, or ``both``. Each file is
    written through the same atomic helper used for output files, so an
    interruption can never leave a truncated digest.
    """
    if digest_format == "json":
        wanted = [(DIGEST_JSON, render_digest_json(state))]
    elif digest_format == "both":
        wanted = [
            (DIGEST_MARKDOWN, render_digest_markdown(state)),
            (DIGEST_JSON, render_digest_json(state)),
        ]
    else:
        wanted = [(DIGEST_MARKDOWN, render_digest_markdown(state))]

    written: list[Path] = []
    directory.mkdir(parents=True, exist_ok=True)
    _restrict(directory, 0o700, diag)
    for filename, content in wanted:
        path = directory / filename
        with atomic_write(path) as handle:
            handle.write(content)
        _restrict(path, 0o600, diag)
        written.append(path)
    return written


def _restrict(path: Path, mode: int, diag: Diagnostics | None) -> None:
    try:
        path.chmod(mode)
    except OSError as exc:  # pragma: no cover - filesystem dependent
        if diag:
            diag.verbose_warn(f"could not restrict permissions on {path}: {exc}")
