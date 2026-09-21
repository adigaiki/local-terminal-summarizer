"""Engine factory: map a configured backend name to an engine instance.

The mapping is data-driven through :mod:`summarizer.engine.backends`. Adding
an OpenAI-compatible local runtime is a catalogue entry, not a new branch
here; adding a runtime with unusual behaviour is a new adapter class selected
by its spec's ``kind``.
"""

from __future__ import annotations

from summarizer.config import Config
from summarizer.engine.backends import (
    KIND_OLLAMA,
    KIND_OPENAI_COMPATIBLE,
    BackendSpec,
    known_backends,
    resolve_backend_spec,
)
from summarizer.engine.base import Engine
from summarizer.engine.client import HttpClient
from summarizer.engine.openai import OpenAICompatEngine
from summarizer.engine.ollama import OllamaEngine
from summarizer.errors import ConfigError
from summarizer.log import Diagnostics

__all__ = ["create_engine", "KNOWN_BACKENDS"]

#: Canonical names, kept for backwards compatibility with callers/imports.
KNOWN_BACKENDS = known_backends()


def _build(spec: BackendSpec, settings, *, diag, http) -> Engine:
    """Instantiate the adapter selected by a spec's ``kind``."""
    if spec.kind == KIND_OLLAMA:
        return OllamaEngine(settings, spec=spec, diag=diag, http=http)
    if spec.kind == KIND_OPENAI_COMPATIBLE:
        return OpenAICompatEngine(settings, spec=spec, diag=diag, http=http)
    # A spec exists but no adapter is wired up for its kind: a programming
    # error in this repository, reported clearly rather than ignored.
    raise ConfigError(
        f"backend {spec.name!r} declares unsupported adapter kind {spec.kind!r}",
        hint="this is a bug in the backend catalogue; please report it",
    )


def create_engine(
    config: Config,
    *,
    diag: Diagnostics | None = None,
    http: HttpClient | None = None,
) -> Engine:
    """Build the configured engine.

    `http` is injectable for tests. The endpoint is always configuration, so
    nothing here hardcodes a default URL anywhere else in the codebase.
    """
    settings = config.engine
    spec = resolve_backend_spec(settings.backend)
    if spec is None:
        raise ConfigError(
            f"unknown engine backend {settings.backend!r}",
            hint=f"supported backends: {', '.join(KNOWN_BACKENDS)}",
        )
    return _build(spec, settings, diag=diag, http=http)
