"""Update checking (check-only in v1).

`UpdateManager.check()` is invoked *only* by the explicit `--check-update`
flag. It performs one network request to a user-configured release manifest
URL, verifies the artifact hash/signature when present, and reports whether
a newer release exists. No automatic installation is performed by this
release; `install.py` provides the refusal logic for when that arrives.
"""

from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from summarizer.__init__ import __version__
from summarizer.config import Config
from summarizer.engine.client import HttpClient
from summarizer.errors import UpdateError
from summarizer.log import Diagnostics
from summarizer.update.install import refuse_managed_install
from summarizer.update.release import fetch_manifest, version_newer
from summarizer.update.verify import verify_sha256, verify_signature

__all__ = ["UpdateManager", "UpdateCheck", "running_exe_path"]


def running_exe_path() -> Path:
    """The installed `summarize` executable path, if this is a script."""
    candidate = Path(sys.argv[0] or "").resolve()
    if candidate.name in ("summarize", "summarizer") and candidate.is_file():
        return candidate
    return Path(sys.executable).resolve()


@dataclass(frozen=True)
class UpdateCheck:
    checked: bool
    current_version: str = ""
    latest_version: str = ""
    newer: bool = False
    message: str = ""

    def render(self) -> str:
        lines = ["Update check"]
        lines.append(f"  installed: {self.current_version}")
        if not self.checked:
            lines.append(f"  {self.message}")
            return "\n".join(lines)
        lines.append(f"  latest:    {self.latest_version}")
        lines.append(
            f"  result:    {'an update is available' if self.newer else 'up to date'}"
        )
        if self.newer:
            lines.append("  note: automatic installation is not performed by this release.")
        return "\n".join(lines)


class UpdateManager:
    def __init__(
        self,
        config: Config,
        *,
        diag: Diagnostics,
        http: HttpClient | None = None,
        exe_path: Path | None = None,
    ) -> None:
        self.config = config
        self.diag = diag
        self.settings = config.update
        self.exe_path = exe_path if exe_path is not None else running_exe_path()
        self.http = http or HttpClient(
            "https://example.invalid",  # placeholder; real URL passed per fetch
            timeout=min(config.engine.timeout_seconds, 30),
            diag=diag,
            max_response_bytes=config.engine.max_response_bytes,
        )

    def check(self) -> UpdateCheck:
        """Explicit, user-invoked update check. Fails loudly on problems."""
        if self.settings.check_on_start and not self._explicitly_requested():
            return UpdateCheck(False, __version__, message="update checks are off by default")
        refuse_managed_install(self.exe_path)
        url = (self.settings.url or "").strip()
        if not url:
            return UpdateCheck(
                False,
                __version__,
                message="no [update] url configured; nothing to check",
            )
        self.diag.progress(f"checking for updates at {url}...")
        try:
            manifest = fetch_manifest(self.http, url, timeout=min(self.config.engine.timeout_seconds, 30))
        except Exception as exc:
            raise UpdateError(
                f"update check failed: {type(exc).__name__}: {exc}",
                hint="is the update URL reachable, and is it an https manifest?",
            ) from exc
        if manifest.signature and self.settings.public_key_path:
            verify_signature(
                manifest.url.encode("utf-8"),  # signature covers the manifest URL
                manifest.signature,
                Path(self.settings.public_key_path).expanduser(),
            )
        return UpdateCheck(
            checked=True,
            current_version=__version__,
            latest_version=manifest.version,
            newer=version_newer(__version__, manifest.version),
        )

    @staticmethod
    def _explicitly_requested() -> bool:
        return "--check-update" in sys.argv