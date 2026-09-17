"""Engine factory: map config backends to engine instances."""

from __future__ import annotations

from summarizer.config import Config
from summarizer.engine.base import Engine
from summarizer.engine.client import HttpClient
from summarizer.engine.openai import OpenAICompatEngine
from summarizer.engine.ollama import OllamaEngine
from summarizer.errors import ConfigError
from summarizer.log import Diagnostics

__all__ = ["create_engine", "KNOWN_BACKENDS"]

KNOWN_BACKENDS = ("openai", "openai-compatible", "ollama")


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
    backend = (config.engine.backend or "ollama").strip().lower()
    settings = config.engine
    if backend in ("openai", "openai-compatible"):
        return OpenAICompatEngine(settings, diag=diag, http=http)
    if backend == "ollama":
        return OllamaEngine(settings, diag=diag, http=http)
    raise ConfigError(
        f"unknown engine backend {config.engine.backend!r}",
        hint=f"supported backends: {', '.join(KNOWN_BACKENDS)}",
    )