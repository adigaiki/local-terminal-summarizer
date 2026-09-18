"""Named, explicitly-selected sessions backed by ordinary local files.

Design (v0.4; the exact semantics are documented in README):

  * A session is a named directory of plain files under the sessions root
    (``~/.local/share/summarizer/sessions/<name>/``). No database, no daemon,
    no background activity.
  * A run records into a session only when that session is *selected*:

        1. ``--no-session``            -> never record
        2. ``--session NAME``          -> that session
        3. ``$SUMMARIZER_SESSION``     -> that session
        4. otherwise, the single active session whose recorded working
           directory matches the run's working directory
        5. otherwise                   -> nothing is recorded

    Unrelated terminals therefore never inherit a session by accident: they
    differ in working directory or simply do not export the variable. If two
    active sessions match one directory the choice is ambiguous and nothing is
    recorded (the tool never guesses).
  * Sessions are never closed or deleted automatically; a session older than
    ``[session] max_age_hours`` only produces a warning.
  * ``state.json`` is the source of truth; ``digest.md``/``digest.json`` are
    rendered from it atomically on every change, so an interruption cannot
    leave a half-written digest. Directories are 0700 and files 0600.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from summarizer.errors import InputError
from summarizer.log import Diagnostics
from summarizer.session.digest import DIGEST_JSON, DIGEST_MARKDOWN, write_digest
from summarizer.session.lock import FileLock

__all__ = ["SessionManager", "SessionInfo", "SESSION_SCHEMA", "validate_name"]

SESSION_SCHEMA = "summarizer.session.v1"
_STATE_FILE = "state.json"
_LOCK_FILE = "lock"
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_STATES = ("active", "closed")


def validate_name(name: str) -> str:
    """Accept only simple, filesystem-safe session names."""
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise InputError(
            f"invalid session name {name!r}",
            hint="use 1-64 letters, digits, dot, underscore or hyphen "
                 "(must start with a letter or digit)",
        )
    return name


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse(timestamp: Any) -> datetime | None:
    if not isinstance(timestamp, str):
        return None
    try:
        moment = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def human_age(seconds: float) -> str:
    """Compact duration for status output: ``5h 22m``, ``3d 1h``, ``12m``."""
    total = max(0, int(seconds))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m"
    return f"{secs}s"


@dataclass(frozen=True)
class SessionInfo:
    """A session as reported to the user and to JSON consumers."""

    name: str
    id: str
    state: str
    created: str
    last_activity: str
    closed_at: str | None
    cwd: str | None
    runs: int
    age_seconds: float
    stale: bool
    directory: Path
    digest_path: Path | None = None

    @property
    def active(self) -> bool:
        return self.state == "active"

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "id": self.id,
            "state": self.state,
            "created": self.created,
            "last_activity": self.last_activity,
            "closed_at": self.closed_at,
            "cwd": self.cwd,
            "runs": self.runs,
            "age_seconds": round(self.age_seconds, 3),
            "age_human": human_age(self.age_seconds),
            "stale": self.stale,
            "directory": str(self.directory),
            "digest_path": str(self.digest_path) if self.digest_path else None,
        }

    def describe(self) -> str:
        lines = [
            self.name,
            f"  state: {self.state}",
            f"  created: {_display(self.created)}",
            f"  last activity: {_display(self.last_activity)}",
            f"  runs: {self.runs}",
            f"  age: {human_age(self.age_seconds)}",
        ]
        if self.cwd:
            lines.append(f"  directory: {self.cwd}")
        return "\n".join(lines)


def _display(timestamp: str | None) -> str:
    moment = _parse(timestamp)
    return moment.astimezone().strftime("%Y-%m-%d %H:%M") if moment else "?"


class SessionManager:
    """Create, select, inspect and close named file-backed sessions."""

    def __init__(
        self,
        *,
        root: Path | None = None,
        diag: Diagnostics | None = None,
        max_age_hours: float = 24.0,
        digest_format: str = "markdown",
    ) -> None:
        from summarizer.config import data_dir

        self.root = Path(root) if root is not None else data_dir() / "sessions"
        self.diag = diag
        self.digest_format = digest_format if digest_format in ("markdown", "json", "both") else "markdown"
        try:
            self.max_age_hours = max(0.0, float(max_age_hours))
        except (TypeError, ValueError):
            self.max_age_hours = 24.0

    # -- paths and state ---------------------------------------------------

    @property
    def sessions_root(self) -> Path:
        return self.root

    def session_dir(self, name: str) -> Path:
        return self.root / validate_name(name)

    def _ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            self.root.chmod(0o700)
        except OSError:  # pragma: no cover - filesystem dependent
            pass

    def _read_state(self, directory: Path) -> dict[str, Any]:
        try:
            data = json.loads((directory / _STATE_FILE).read_text(encoding="utf-8"))
        except (FileNotFoundError, NotADirectoryError):
            return {}
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            if self.diag:
                self.diag.warn(f"ignoring unreadable session state in {directory}")
            return {}
        return data if isinstance(data, dict) else {}

    def _write_state(self, directory: Path, state: dict[str, Any]) -> None:
        from summarizer.output.atomic import atomic_write

        self._ensure_root()
        directory.mkdir(parents=True, exist_ok=True)
        try:
            directory.chmod(0o700)
        except OSError:  # pragma: no cover
            pass
        path = directory / _STATE_FILE
        with atomic_write(path) as handle:
            handle.write(json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
        try:
            path.chmod(0o600)
        except OSError:  # pragma: no cover
            pass

    def _lock(self, directory: Path) -> FileLock:
        return FileLock(directory / _LOCK_FILE)

    # -- inspection ---------------------------------------------------------

    def list_sessions(self) -> list[SessionInfo]:
        """Every session under the root; unreadable entries are skipped."""
        if not self.root.is_dir():
            return []
        infos: list[SessionInfo] = []
        for entry in sorted(self.root.iterdir()):
            if not entry.is_dir():
                continue
            state = self._read_state(entry)
            if not state.get("name"):
                continue
            infos.append(self._info_from(entry, state))
        return infos

    def get(self, name: str) -> SessionInfo:
        directory = self.session_dir(name)
        state = self._read_state(directory)
        if not state.get("name"):
            known = ", ".join(info.name for info in self.list_sessions()) or "none"
            raise InputError(
                f"no such session: {name!r}",
                hint=f"start it with `summarize session start {name}`; known sessions: {known}",
            )
        return self._info_from(directory, state)

    def _info_from(self, directory: Path, state: dict[str, Any]) -> SessionInfo:
        runs = [run for run in state.get("runs", []) if isinstance(run, dict)]
        created = str(state.get("created") or "")
        moment = _parse(created)
        now = datetime.now(timezone.utc)
        age = (now - moment).total_seconds() if moment else 0.0
        state_name = str(state.get("state") or "closed")
        stale = state_name == "active" and age > self.max_age_hours * 3600
        digest = directory / DIGEST_MARKDOWN
        if not digest.exists():
            candidate = directory / DIGEST_JSON
            digest = candidate if candidate.exists() else digest
        return SessionInfo(
            name=str(state.get("name")),
            id=str(state.get("id") or "?"),
            state=state_name if state_name in _STATES else "closed",
            created=created,
            last_activity=str(state.get("last_activity") or created),
            closed_at=state.get("closed_at"),
            cwd=state.get("cwd"),
            runs=len(runs),
            age_seconds=age,
            stale=stale,
            directory=directory,
            digest_path=digest,
        )

    # -- lifecycle ----------------------------------------------------------

    def start(self, name: str) -> SessionInfo:
        """Create (or resume) a named session and mark it active."""
        directory = self.session_dir(name)
        with self._lock(directory):
            state = self._read_state(directory)
            if state.get("state") == "active":
                raise InputError(
                    f"session {name!r} is already active",
                    hint="end it first (`summarize session end NAME`), or use another name",
                )
            existing = [run for run in state.get("runs", []) if isinstance(run, dict)]
            now = _now()
            state = {
                "schema": SESSION_SCHEMA,
                "name": name,
                "id": str(state.get("id") or f"{name}-{secrets.token_hex(6)}"),
                "state": "active",
                "created": now,
                "last_activity": now,
                "closed_at": None,
                "cwd": str(Path.cwd()),
                "runs": existing,
            }
            self._write_state(directory, state)
            write_digest(directory, state, digest_format=self.digest_format, diag=self.diag)
        return self._info_from(directory, state)

    def end(self, name: str | None = None) -> SessionInfo:
        """Finalize the digest and close a session. Never deletes anything."""
        target = name or self._resolve_single_active()
        directory = self.session_dir(target)
        with self._lock(directory):
            state = self._read_state(directory)
            if not state.get("name"):
                known = ", ".join(info.name for info in self.list_sessions()) or "none"
                raise InputError(
                    f"no such session: {target!r}",
                    hint=f"known sessions: {known}",
                )
            if state.get("state") != "active":
                raise InputError(
                    f"session {target!r} is already closed",
                    hint="start a new session with `summarize session start NAME`",
                )
            state["state"] = "closed"
            state["closed_at"] = _now()
            state["last_activity"] = state["closed_at"]
            self._write_state(directory, state)
            write_digest(directory, state, digest_format=self.digest_format, diag=self.diag)
        return self._info_from(directory, state)

    def status(self, name: str | None = None) -> list[SessionInfo]:
        return [self.get(name)] if name else self.list_sessions()

    # -- selection ----------------------------------------------------------

    def select(
        self,
        *,
        explicit: str | None = None,
        env_name: str | None = None,
        cwd: Path | None = None,
    ) -> SessionInfo | None:
        """Resolve which session a run records into; ``None`` means none.

        Explicit selections fail loudly. The working-directory rule is
        conservative by design: it never guesses, so an unrelated terminal
        cannot inherit a session it did not ask for.
        """
        if explicit:
            info = self.get(explicit)
            if not info.active:
                raise InputError(
                    f"session {explicit!r} is closed",
                    hint=f"start it with `summarize session start {explicit}`",
                )
            return info
        if env_name:
            info = self.get(env_name)
            if not info.active:
                raise InputError(
                    f"session {env_name!r} (from $SUMMARIZER_SESSION) is closed",
                    hint=f"start it with `summarize session start {env_name}`",
                )
            return info
        if cwd is None:
            return None
        matches = [
            info for info in self.list_sessions()
            if info.active and info.cwd == str(cwd)
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1 and self.diag:
            names = ", ".join(info.name for info in matches)
            self.diag.warn(
                f"multiple active sessions match this directory ({names}); "
                "not recording -- use --session NAME"
            )
        return None

    def _resolve_single_active(self) -> str:
        active = [info for info in self.list_sessions() if info.active]
        if not active:
            raise InputError(
                "no active session to end",
                hint="start one with `summarize session start NAME`",
            )
        if len(active) == 1:
            return active[0].name
        here = [info for info in active if info.cwd == str(Path.cwd())]
        if len(here) == 1:
            return here[0].name
        names = ", ".join(info.name for info in active)
        raise InputError(
            "multiple active sessions: NAME is required",
            hint=f"run `summarize session end NAME`; active sessions: {names}",
        )

    # -- recording ----------------------------------------------------------

    def record(self, name: str, run: Any, *, digest_format: str | None = None) -> Path | None:
        """Append one compact run pointer under the session lock."""
        directory = self.session_dir(name)
        with self._lock(directory):
            state = self._read_state(directory)
            if state.get("state") != "active":
                raise InputError(
                    f"session {name!r} is not active",
                    hint=f"start it with `summarize session start {name}`",
                )
            runs = state.get("runs")
            if not isinstance(runs, list):
                runs = []
            runs.append(run.to_json())
            state["runs"] = runs
            state["last_activity"] = _now()
            self._write_state(directory, state)
            written = write_digest(
                directory,
                state,
                digest_format=digest_format or self.digest_format,
                diag=self.diag,
            )
        return written[0] if written else None


def render_status(infos: list[SessionInfo], *, max_age_hours: float) -> str:
    """Human-readable `session status`, including stale-session warnings."""
    if not infos:
        return "No sessions yet. Start one with `summarize session start NAME`.\n"
    active = [info for info in infos if info.active]
    lines: list[str] = []
    if active:
        lines += ["Active sessions:", ""]
        for info in active:
            lines += [info.describe(), ""]
            if info.stale:
                lines += [
                    "Warning: session is older than configured max_age_hours "
                    f"({human_age(max_age_hours * 3600)}); it was not closed or "
                    "deleted automatically.",
                    "",
                ]
    else:
        lines += ["No active sessions.", ""]
    closed = [info for info in infos if not info.active]
    if closed:
        lines += ["Closed sessions:", ""]
        for info in closed:
            lines.append(
                f"  {info.name}  closed: {_display(info.closed_at)}  runs: {info.runs}"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
