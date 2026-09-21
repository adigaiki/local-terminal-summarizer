"""Optional local cache and checkpoints for map-stage results.

Design goals, in order:

  * **Off by default.** Nothing is written unless ``[cache] mode`` enables it.
  * **Self-invalidating.** Every entry is keyed by a content-addressed
    identity: document hash, model, backend, effective profile/prompt
    identity, chunking settings, relevant engine parameters, and the exact
    per-chunk content hashes. If any of those change, the key changes and old
    results are simply not found (they are also never silently reused across
    configurations).
  * **Never stores the document.** Only derived summaries and hashes are
    stored; the original document is never re-written and source paths are
    not stored.
  * **Bounded.** ``max_entries`` and ``max_bytes`` prune oldest-first; a
    single oversized result is skipped via ``max_chunk_bytes``.
  * **Private by default.** Directories are 0700 and files 0600.

A partial entry (``status: in_progress``) is a checkpoint: a later run with
the same identity can resume the chunks that completed, then fill the rest.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from summarizer.config import CacheSettings
from summarizer.log import Diagnostics

__all__ = [
    "SCHEMA",
    "CacheIdentity",
    "CacheStatus",
    "CacheEntry",
    "LocalCache",
    "content_hash",
]

SCHEMA = "summarizer.cache.v1"
_MANIFEST = "manifest.json"
_RESULTS = "results"


def content_hash(text: str) -> str:
    """Hash content for cache keying. Never reversible, never stored raw."""
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _digest(payload: object) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class CacheIdentity:
    """Everything that must match for a previous result to be reusable."""

    document_hash: str
    model: str
    backend: str
    profile: str
    profile_identity: str
    prompt_identity: str
    output_format: str
    lang: str
    context_hash: str
    chunk_hashes: tuple[str, ...]
    chunking: dict[str, Any] = field(default_factory=dict)
    engine: dict[str, Any] = field(default_factory=dict)
    schema: str = SCHEMA

    @property
    def key(self) -> str:
        return _digest(self.to_json())[:32]

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "document_hash": self.document_hash,
            "model": self.model,
            "backend": self.backend,
            "profile": self.profile,
            "profile_identity": self.profile_identity,
            "prompt_identity": self.prompt_identity,
            "output_format": self.output_format,
            "lang": self.lang,
            "context_hash": self.context_hash,
            "chunk_hashes": list(self.chunk_hashes),
            "chunking": dict(self.chunking),
            "engine": dict(self.engine),
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "CacheIdentity":
        return cls(
            document_hash=str(data.get("document_hash", "")),
            model=str(data.get("model", "")),
            backend=str(data.get("backend", "")),
            profile=str(data.get("profile", "")),
            profile_identity=str(data.get("profile_identity", "")),
            prompt_identity=str(data.get("prompt_identity", "")),
            output_format=str(data.get("output_format", "")),
            lang=str(data.get("lang", "")),
            context_hash=str(data.get("context_hash", "")),
            chunk_hashes=tuple(str(h) for h in data.get("chunk_hashes", [])),
            chunking=dict(data.get("chunking") or {}),
            engine=dict(data.get("engine") or {}),
            schema=str(data.get("schema", SCHEMA)),
        )


@dataclass
class CacheEntry:
    """A loaded manifest plus any completed chunk summaries."""

    identity: CacheIdentity
    status: str
    complete: bool
    summaries: dict[int, str] = field(default_factory=dict)

    @property
    def completed(self) -> int:
        return len(self.summaries)


@dataclass
class CacheStatus:
    mode: str
    path: Path
    entries: int
    bytes: int
    max_entries: int
    max_bytes: int
    complete: int
    in_progress: int
    oldest: str | None
    newest: str | None

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": "summarizer.cache.status.v1",
            "mode": self.mode,
            "path": str(self.path),
            "entries": self.entries,
            "bytes": self.bytes,
            "complete": self.complete,
            "in_progress": self.in_progress,
            "max_entries": self.max_entries,
            "max_bytes": self.max_bytes,
            "oldest": self.oldest,
            "newest": self.newest,
        }


class LocalCache:
    """File-backed cache under ``[cache] dir``. No database, no daemon."""

    def __init__(self, settings: CacheSettings, *, diag: Diagnostics | None = None) -> None:
        self.settings = settings
        self.diag = diag
        self.root = Path(settings.dir).expanduser()

    # -- policy --------------------------------------------------------------

    @property
    def read_enabled(self) -> bool:
        return self.settings.read_enabled

    @property
    def write_enabled(self) -> bool:
        return self.settings.write_enabled

    @property
    def enabled(self) -> bool:
        return self.read_enabled or self.write_enabled

    # -- paths / permissions -------------------------------------------------

    def entry_dir(self, identity: CacheIdentity) -> Path:
        return self.root / _RESULTS / identity.key

    def _ensure_entry(self, identity: CacheIdentity) -> Path:
        directory = self.entry_dir(identity)
        directory.mkdir(parents=True, exist_ok=True)
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            self.root.chmod(0o700)
            (self.root / _RESULTS).chmod(0o700)
        except OSError:  # pragma: no cover - filesystem dependent
            pass
        try:
            directory.chmod(0o700)
        except OSError:  # pragma: no cover
            pass
        return directory

    def _chunk_path(self, identity: CacheIdentity, index: int) -> Path:
        return self.entry_dir(identity) / f"chunk-{index:05d}.json"

    # -- reading -------------------------------------------------------------

    def load(self, identity: CacheIdentity) -> CacheEntry | None:
        """Load a compatible entry, or ``None`` when absent/incompatible."""
        if not self.read_enabled:
            return None
        manifest = self._read_manifest(identity)
        if manifest is None:
            return None
        stored = manifest.get("identity")
        if not isinstance(stored, dict):
            return None
        # Defence in depth: the stored identity must match exactly. Any
        # difference (model, profile, chunk plan, engine config, ...) means we
        # must not reuse these results.
        if CacheIdentity.from_json(stored) != identity:
            return None
        status = str(manifest.get("status", "in_progress"))
        summaries: dict[int, str] = {}
        for index in range(len(identity.chunk_hashes)):
            path = self._chunk_path(identity, index)
            data = _read_json(path)
            if isinstance(data, dict) and isinstance(data.get("summary"), str):
                summaries[index] = data["summary"]
        complete = status == "complete" and len(summaries) == len(identity.chunk_hashes)
        return CacheEntry(identity=identity, status=status, complete=complete, summaries=summaries)

    def _read_manifest(self, identity: CacheIdentity) -> dict[str, Any] | None:
        data = _read_json(self.entry_dir(identity) / _MANIFEST)
        return data if isinstance(data, dict) else None

    # -- writing -------------------------------------------------------------

    def save_chunk(self, identity: CacheIdentity, index: int, summary: str) -> None:
        """Persist one completed map result, then bound the cache size."""
        if not self.write_enabled:
            return
        encoded = summary.encode("utf-8", "replace")
        if self.settings.max_chunk_bytes and len(encoded) > self.settings.max_chunk_bytes:
            if self.diag:
                self.diag.verbose_warn(
                    f"not caching chunk {index + 1}: result exceeds "
                    f"max_chunk_bytes ({len(encoded)} bytes)"
                )
            return
        directory = self._ensure_entry(identity)
        if not (directory / _MANIFEST).is_file():
            self._write_manifest(identity, status="in_progress")
        self._write_json(
            self._chunk_path(identity, index),
            {"schema": SCHEMA, "index": index, "summary": summary},
        )
        self._prune()

    def mark_complete(self, identity: CacheIdentity) -> None:
        if not self.write_enabled:
            return
        self._write_manifest(identity, status="complete")

    def _write_manifest(self, identity: CacheIdentity, *, status: str) -> None:
        path = self.entry_dir(identity) / _MANIFEST
        existing = _read_json(path) or {}
        payload = {
            "schema": SCHEMA,
            "status": status,
            "identity": identity.to_json(),
            "created": existing.get("created") or _now(),
            "updated": _now(),
        }
        self._write_json(path, payload)

    def _write_json(self, path: Path, payload: Any) -> None:
        from summarizer.output.atomic import atomic_write

        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with atomic_write(path) as handle:
                handle.write(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
            path.chmod(0o600)
        except OSError as exc:  # a cache failure must never break a run
            if self.diag:
                self.diag.verbose_warn(f"cache write failed: {exc}")

    # -- maintenance ---------------------------------------------------------

    def _entry_dirs(self) -> list[Path]:
        results = self.root / _RESULTS
        if not results.is_dir():
            return []
        return [entry for entry in results.iterdir() if entry.is_dir()]

    def _prune(self) -> None:
        """Bound the cache by entry count and total bytes, oldest first."""
        max_entries = int(self.settings.max_entries or 0)
        max_bytes = int(self.settings.max_bytes or 0)
        if not max_entries and not max_bytes:
            return
        dirs = sorted(self._entry_dirs(), key=lambda p: _mtime(p))
        total = sum(_dir_size(d) for d in dirs)
        # Remove oldest while either bound is exceeded.
        while dirs and (
            (max_entries and len(dirs) > max_entries)
            or (max_bytes and total > max_bytes)
        ):
            oldest = dirs.pop(0)
            total -= _dir_size(oldest)
            shutil.rmtree(oldest, ignore_errors=True)

    def status(self) -> CacheStatus:
        dirs = self._entry_dirs()
        complete = in_progress = 0
        for directory in dirs:
            manifest = _read_json(directory / _MANIFEST)
            if isinstance(manifest, dict) and manifest.get("status") == "complete":
                complete += 1
            else:
                in_progress += 1
        moments = sorted(_mtime(d) for d in dirs)
        return CacheStatus(
            mode=self.settings.mode,
            path=self.root,
            entries=len(dirs),
            bytes=sum(_dir_size(d) for d in dirs),
            max_entries=int(self.settings.max_entries or 0),
            max_bytes=int(self.settings.max_bytes or 0),
            complete=complete,
            in_progress=in_progress,
            oldest=_iso(moments[0]) if moments else None,
            newest=_iso(moments[-1]) if moments else None,
        )

    def clear(self) -> int:
        """Remove all cached entries. Returns the number removed."""
        removed = 0
        for directory in self._entry_dirs():
            shutil.rmtree(directory, ignore_errors=True)
            removed += 1
        return removed


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def _dir_size(directory: Path) -> int:
    total = 0
    for path in directory.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def _mtime(directory: Path) -> float:
    try:
        return directory.stat().st_mtime
    except OSError:
        return 0.0


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(timespec="seconds")
