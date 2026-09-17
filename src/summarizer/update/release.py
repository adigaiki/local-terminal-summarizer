"""Release manifest handling for the opt-in update check.

Update checking is *explicitly opt-in* (the `--check-update` flag). Nothing
here is called during ordinary summarization, and this code never installs
anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from summarizer.engine.client import HttpClient
from summarizer.errors import UpdateError

__all__ = ["ReleaseManifest", "parse_manifest", "fetch_manifest", "version_newer", "validate_version"]


@dataclass(frozen=True)
class ReleaseManifest:
    version: str
    url: str
    sha256: str | None = None
    signature: str | None = None  # base64-encoded detached signature
    notes: str = ""

    @property
    def is_signed(self) -> bool:
        return bool(self.signature)

    @property
    def is_verifiable(self) -> bool:
        return not self.is_signed or bool(self.sha256)  # sha256 always usable


def validate_version(version: str) -> None:
    if not version or not version.strip() or any(ch.isspace() for ch in version):
        raise UpdateError(f"invalid release version {version!r} in manifest")


def parse_manifest(data: dict[str, Any]) -> ReleaseManifest:
    try:
        version = str(data["version"])
        url = str(data["url"])
    except KeyError as exc:
        raise UpdateError(f"release manifest is missing required key {exc.args[0]!r}") from exc
    validate_version(version)
    if not urlparse(url).scheme in ("https", "http"):
        raise UpdateError(f"release manifest has an invalid download URL {url!r}")
    return ReleaseManifest(
        version=version,
        url=url,
        sha256=str(data["sha256"]) if data.get("sha256") else None,
        signature=str(data["signature"]) if data.get("signature") else None,
        notes=str(data.get("notes", "")),
    )


def fetch_manifest(
    http: HttpClient,
    url: str,
    *,
    timeout: float | None = None,
) -> ReleaseManifest:
    """GET a release manifest and parse it. This is the only network call in
    the update module, and the caller invokes it explicitly."""
    raw = http.get_json(url, timeout=timeout)
    if not isinstance(raw, dict):
        raise UpdateError(f"release endpoint at {url} returned non-object data")
    return parse_manifest(raw)


def _parse_component(part: str) -> tuple:
    out = ()
    for token in part.split("."):
        if token.isdigit():
            out += (int(token),)
        else:
            out += (token,)
    return out


def version_newer(local: str, remote: str) -> bool:
    """True when `remote` sorts newer than `local` (permissive semver-ish)."""
    if local == remote:
        return False
    lp, rp = _parse_component(local), _parse_component(remote)
    for l, r in zip(lp, rp):
        if isinstance(l, int) and isinstance(r, int):
            if l != r:
                return r > l
        elif l != r:
            return str(r) > str(l)
    return len(rp) > len(lp)