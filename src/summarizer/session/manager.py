"""Session state with explicit semantics.

v1 semantics (documented in README):

  * There is exactly one active session at a time, per user data directory
    (`$XDG_DATA_HOME/summarizer` or `~/.local/share/summarizer`).
  * `session start NAME` records a state file (``session.json``) plus the
    PID of the starting process. If a session is already active, `start`
    fails with a hint unless the stale PID is dead.
  * `session end` clears the state. Sessions do not automatically expire.
  * When `[session] enabled = true` and a session is active, the pipeline
    appends a compact note (timestamp/source/profile/one-sentence extract)
    to the notes digest. Full summaries are never written to session files.
  * Unrelated terminals share the user-level session unless they `end` it —
    sessions are scoped to the user, not to a terminal. This is documented
    as a deliberate v1 simplification.

All state is stored with restrictive permissions (0o700 directories,
0o600 files) and writes are serialized with flock.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from summarizer.errors import InputError
from summarizer.log import Diagnostics
from summarizer.session.lock import FileLock
from summarizer.config import data_dir as default_data_dir

__all__ = ["SessionManager", "SessionStatus"]

_STATE_FILE = "session.json"
_LOCK_FILE = "session.lock"


@dataclass(frozen=True)
class SessionStatus:
    active: bool
    name: str | None = None
    started: str | None = None
    pid: int | None = None
    stale: bool = False

    def describe(self) -> str:
        if not self.active:
            return "no active session"
        return (
            f"session {self.name!r} active since {self.started} "
            f"(pid {self.pid})"
            + (" [stale: process not running]" if self.stale else "")
        )


class SessionManager:
    def __init__(
        self,
        *,
        state_dir: Path | None = None,
        diag: Diagnostics | None = None,
    ) -> None:
        self.state_dir = state_dir if state_dir is not None else default_data_dir()
        self.state_file = self.state_dir / _STATE_FILE
        self.lock_file = self.state_dir / _LOCK_FILE
        self.diag = diag

    def _ensure_dirs(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.state_dir.chmod(0o700)
        except OSError:
            pass

    def _read_state(self) -> dict[str, Any]:
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (json.JSONDecodeError, OSError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_state(self, state: dict[str, Any]) -> None:
        self._ensure_dirs()
        tmp = self.state_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        try:
            tmp.chmod(0o600)
        except OSError:
            pass
        os.replace(tmp, self.state_file)

    def start(self, name: str) -> SessionStatus:
        if not name or name.strip() != name or "/" in name:
            raise InputError(
                f"invalid session name {name!r}",
                hint="session names must be simple, non-empty strings",
            )
        with FileLock(self.lock_file):
            current = self._read_state()
            if current.get("active"):
                existing_pid = current.get("pid")
                if existing_pid and _pid_alive(existing_pid):
                    raise InputError(
                        f"a session is already active: {current.get('name', '?')} "
                        f"(started {current.get('started', '?')})",
                        hint="run `summarize session end` first, or choose another "
                             "user data directory",
                    )
            state = {
                "active": True,
                "name": name,
                "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "pid": os.getpid(),
            }
            self._write_state(state)
        return SessionStatus(
            active=True,
            name=state.get("name"),
            started=state.get("started"),
            pid=state.get("pid"),
        )

    def end(self) -> SessionStatus:
        with FileLock(self.lock_file):
            current = self._read_state()
            was_active = bool(current.get("active"))
            self._write_state({})
        return SessionStatus(active=False, name=current.get("name"), started=current.get("started"))

    def status(self) -> SessionStatus:
        with FileLock(self.lock_file):
            state = self._read_state()
        if not state.get("active"):
            return SessionStatus(active=False)
        pid = state.get("pid")
        stale = bool(pid) and not _pid_alive(pid)
        return SessionStatus(
            active=True,
            name=state.get("name"),
            started=state.get("started"),
            pid=pid,
            stale=stale,
        )


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True