"""Capability model for engine backends.

Backends and models differ widely. We never assume a backend supports
streaming, structured JSON, model listing, or any particular context length:
capabilities are *discovered* from the local server where possible, cached,
and consumed by the pipeline for budgeting.

A discovered value always carries its provenance (``context_source``) so the
pipeline, ``--dry-run`` and ``summarize doctor`` can distinguish "the server
told us" from "we assumed a conservative default". There is no universal
context size, and this module never hardcodes behaviour for a specific model
family.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

__all__ = [
    "EngineCapabilities",
    "DEFAULT_CONTEXT_LENGTH",
    "CONTEXT_SOURCE_CONFIG",
    "CONTEXT_SOURCE_SERVER",
    "CONTEXT_SOURCE_FALLBACK",
]

# Conservative fallback used only when the backend cannot report anything
# about its context window. Deliberately modest so prompts fit on small local
# models. Never treated as the model's real window.
DEFAULT_CONTEXT_LENGTH = 8192

# Provenance tags for a context length.
CONTEXT_SOURCE_CONFIG = "config"  # explicit [engine] context_length
CONTEXT_SOURCE_SERVER = "server"  # discovered from the local server's API
CONTEXT_SOURCE_FALLBACK = "fallback"  # DEFAULT_CONTEXT_LENGTH was assumed


@dataclass(frozen=True)
class EngineCapabilities:
    """What a backend/model can do, and how we learned it.

    ``structured_json``/``reasoning_control`` use three-valued logic: True the
    backend advertises support, False it refused, None unknown (never assume).
    """

    streaming: bool = True
    structured_json: bool | None = None
    model_listing: bool = True
    # Token-count context window if the backend could tell us, else None.
    context_length: int | None = None
    # Where context_length came from: CONTEXT_SOURCE_* or None when unknown.
    context_source: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)
    # Whether the backend accepts a reasoning-effort field on its chat
    # endpoint. Unknown means omit it rather than risk incompatibility.
    reasoning_control: bool | None = None

    # -- context window -----------------------------------------------------

    @property
    def context_is_known(self) -> bool:
        """True only when the context window was actually discovered/configured."""
        return bool(self.context_length and self.context_length > 0)

    @property
    def effective_context_length(self) -> int:
        """The window to budget against, falling back conservatively."""
        return self.context_length or DEFAULT_CONTEXT_LENGTH

    @property
    def effective_context_source(self) -> str:
        return self.context_source or CONTEXT_SOURCE_FALLBACK

    def with_context_length(
        self,
        length: int | None,
        *,
        source: str | None = None,
    ) -> "EngineCapabilities":
        """Return a copy carrying a context length (no-op when None)."""
        if length is None or length <= 0:
            return self
        return replace(self, context_length=int(length), context_source=source or CONTEXT_SOURCE_SERVER)

    def without_context(self) -> "EngineCapabilities":
        """Return a copy that claims no context knowledge (used by --dry-run)."""
        return replace(self, context_length=None, context_source=None)

    # -- misc --------------------------------------------------------------

    def with_notes(self, *notes: str) -> "EngineCapabilities":
        merged: list[str] = list(self.notes)
        for note in notes:
            if note and note not in merged:
                merged.append(note)
        return replace(self, notes=tuple(merged))

    def describe(self) -> str:
        """One-line human-readable summary used by diagnostics."""
        ctx = (
            f"{self.context_length} tokens ({self.effective_context_source})"
            if self.context_is_known
            else f"unknown; assuming {DEFAULT_CONTEXT_LENGTH} (fallback)"
        )
        return (
            f"context: {ctx}; streaming: {self.streaming}; "
            f"structured json: {_tri(self.structured_json)}; "
            f"reasoning control: {_tri(self.reasoning_control)}"
        )


def _tri(value: bool | None) -> str:
    return "unknown" if value is None else ("yes" if value else "no")
