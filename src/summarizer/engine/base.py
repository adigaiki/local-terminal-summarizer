"""The generic engine interface.

Ollama is not the conceptual core of the application. Anything that can
implement this interface (any local model server, whether or not it speaks
the OpenAI Chat Completions API) is a valid backend; add new backends by
declaring a :class:`~summarizer.engine.backends.BackendSpec` and, when needed,
a small adapter in :mod:`summarizer.engine`.

The pipeline must never ask *which* backend it is talking to. It asks what
the engine *supports* (see :meth:`capabilities` and the ``supports_*``
accessors here) and negotiates from that answer. Backend-specific quirks live
inside adapters, not in the pipeline.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator

from summarizer.engine.backends import BackendSpec
from summarizer.engine.capabilities import EngineCapabilities

__all__ = ["Engine"]


class Engine(ABC):
    """A summarization engine backed by some local (or opt-in) model server."""

    backend: str
    endpoint: str
    model: str
    timeout_seconds: float
    retries: int
    #: Static adapter description when the engine was created through the
    #: factory; optional so engines can be constructed directly in tests.
    spec: BackendSpec | None = None
    #: Backend-reported token usage for the most recent generation, when the
    #: adapter can read it. Best-effort; never comparable across backends.
    last_usage: dict[str, int] | None = None

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

    # -- capability negotiation ---------------------------------------------
    #
    # Thin, intent-revealing accessors the pipeline uses instead of checking a
    # backend name. ``None`` means "unknown", not "no": callers must decide
    # how to treat unknown rather than assuming support.

    def supports_streaming(self) -> bool:
        return self.capabilities().supports_streaming

    def supports_structured_output(self) -> bool | None:
        return self.capabilities().supports_structured_output

    def supports_model_listing(self) -> bool:
        return self.capabilities().supports_model_listing

    def supports_reasoning_control(self) -> bool | None:
        return self.capabilities().supports_reasoning_control

    def describe_capabilities(self) -> str:
        return self.capabilities().describe()

    # -- cancellation --------------------------------------------------------

    def set_cancel(self, cancel: object | None) -> None:
        """Optional hook: let the engine observe cooperative cancellation.

        The base implementation accepts and ignores the token; adapters that
        can interrupt in-flight work (HTTP reads/streams) override it.
        """
        return None
