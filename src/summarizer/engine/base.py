"""The generic engine interface.

Ollama is not the conceptual core of the application. Anything that can
implement this interface (any OpenAI-compatible local server) is a valid
backend; add new backends by writing a small class and registering it in
:mod:`summarizer.engine.factory`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator

from summarizer.engine.capabilities import EngineCapabilities

__all__ = ["Engine"]


class Engine(ABC):
    """A summarization engine backed by some local (or opt-in) model server."""

    backend: str
    endpoint: str
    model: str
    timeout_seconds: float
    retries: int

    @abstractmethod
    def generate(
        self,
        *,
        prompt: str,
        json_object: bool = False,
        temperature: float | None = None,
    ) -> str:
        """Run a single generation, returning the full text."""

    @abstractmethod
    def stream(
        self,
        *,
        prompt: str,
        temperature: float | None = None,
    ) -> Iterator[str]:
        """Yield text deltas as they are produced by the server."""

    @abstractmethod
    def health(self) -> bool:
        """True when the endpoint answers a health/model-list request."""

    @abstractmethod
    def list_models(self) -> list[str]:
        """Names of models the endpoint can serve (empty when unknown)."""

    @abstractmethod
    def capabilities(self) -> EngineCapabilities:
        """Detected/cached capability information for this endpoint+model."""