"""Stable identity for the *effective* prompt/profile configuration.

A checkpoint or cache entry must be able to tell whether a previous result was
produced with the same prompt. Hashing only the profile *name* is not enough:
the user's profile file may have changed, and the same name can resolve to a
different file. The identity therefore hashes the effective template text,
its source kind, the prompt schema version, and the run parameters that
change the rendered prompt (language, output format, whether trusted context
is present).

The identity is a short hex digest. It never contains prompt text, so it is
safe to log, print, and store. The schema version is bumped whenever the
prompt format changes in a way that invalidates old results.
"""

from __future__ import annotations

import hashlib
import json

from summarizer.profiles import Profile

__all__ = ["PROMPT_SCHEMA_VERSION", "profile_identity", "prompt_identity"]

#: Bump when the security preamble or the way prompts are assembled changes,
#: so cached/checkpointed results from an older prompt are never reused.
PROMPT_SCHEMA_VERSION = "summarizer.prompt.v1"


def _digest(payload: object) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def profile_identity(profile: Profile) -> str:
    """A content-addressed id for one profile's effective instructions."""
    return _digest(
        {
            "schema": PROMPT_SCHEMA_VERSION,
            "name": profile.name,
            "source": profile.source,
            "text": profile.text,
            "is_reduce": profile.is_reduce,
        }
    )[:16]


def prompt_identity(
    profile: Profile,
    *,
    lang: str | None,
    output_format: str,
    has_context: bool,
) -> str:
    """Identity of the rendered prompt for a specific run configuration."""
    return _digest(
        {
            "profile": profile_identity(profile),
            "lang": lang or "",
            "output_format": output_format,
            "has_context": bool(has_context),
            "schema": PROMPT_SCHEMA_VERSION,
        }
    )[:16]
