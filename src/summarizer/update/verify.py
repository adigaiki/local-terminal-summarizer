"""Verification for release artifacts.

SHA-256 hashes are verified with the standard library. Detached Ed25519
signatures are verified with `cryptography` when it is installed; when a
manifest carries a signature but no verifier is available, verification is
reported as unavailable rather than silently skipped.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from pathlib import Path

from summarizer.errors import UpdateError

__all__ = ["verify_sha256", "verify_signature"]


def verify_sha256(data: bytes, expected: str) -> None:
    """Raise UpdateError unless the data matches the expected SHA-256 hex."""
    expected = expected.strip().lower()
    if not expected or any(c not in "0123456789abcdef" for c in expected):
        raise UpdateError("release manifest has an invalid sha256 value")
    digest = hashlib.sha256(data).hexdigest()
    if not hmac.compare_digest(digest, expected):
        raise UpdateError(
            "SHA-256 verification failed: downloaded artifact does not match "
            "the manifest (refusing to proceed)",
        )


def verify_signature(data: bytes, signature_b64: str, public_key_path: Path) -> None:
    """Verify a base64 Ed25519 signature over the artifact bytes."""
    if not public_key_path.is_file():
        raise UpdateError(
            f"release is signed but no public key is configured "
            f"(looked for {public_key_path}); refusing to proceed",
        )
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:
        raise UpdateError(
            "release is signed but signature verification is unavailable "
            "(the optional `cryptography` package is not installed)",
            hint="install `cryptography` before verifying signed releases",
        ) from exc
    try:
        sig = base64.b64decode(signature_b64)
    except Exception as exc:
        raise UpdateError("release manifest has invalid base64 signature") from exc
    try:
        public_key = Ed25519PublicKey.from_public_bytes(public_key_path.read_bytes())
        public_key.verify(sig, data)
    except InvalidSignature as exc:
        raise UpdateError("signature verification failed (artifact or key mismatch)") from exc
    except OSError as exc:
        raise UpdateError(f"cannot read the release public key {public_key_path}: {exc}") from exc