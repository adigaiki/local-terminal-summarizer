"""Engine layer: generic interface, capabilities, HTTP client, backends."""

from summarizer.engine.base import Engine
from summarizer.engine.capabilities import DEFAULT_CONTEXT_LENGTH, EngineCapabilities
from summarizer.engine.factory import KNOWN_BACKENDS, create_engine
from summarizer.engine.openai import OpenAICompatEngine
from summarizer.engine.ollama import OllamaEngine

__all__ = [
    "Engine",
    "EngineCapabilities",
    "create_engine",
    "KNOWN_BACKENDS",
    "OpenAICompatEngine",
    "OllamaEngine",
    "DEFAULT_CONTEXT_LENGTH",
]