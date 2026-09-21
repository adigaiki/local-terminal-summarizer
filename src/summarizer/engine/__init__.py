"""Engine layer: generic interface, capabilities, HTTP client, backends."""

from summarizer.engine.backends import (
    BACKEND_SPECS,
    BackendSpec,
    backend_choices,
    canonical_backend_name,
    default_endpoint_for_backend,
    known_backends,
    resolve_backend_spec,
)
from summarizer.engine.base import Engine
from summarizer.engine.capabilities import DEFAULT_CONTEXT_LENGTH, EngineCapabilities
from summarizer.engine.factory import KNOWN_BACKENDS, create_engine
from summarizer.engine.openai import OpenAICompatEngine
from summarizer.engine.ollama import OllamaEngine

__all__ = [
    "Engine",
    "EngineCapabilities",
    "BackendSpec",
    "BACKEND_SPECS",
    "create_engine",
    "resolve_backend_spec",
    "canonical_backend_name",
    "backend_choices",
    "default_endpoint_for_backend",
    "KNOWN_BACKENDS",
    "known_backends",
    "OpenAICompatEngine",
    "OllamaEngine",
    "DEFAULT_CONTEXT_LENGTH",
]
