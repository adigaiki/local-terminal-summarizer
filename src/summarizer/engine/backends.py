"""Declarative catalogue of local backend adapters.

This module is the single place that knows backend *names*. Adding a local
runtime is either:

  * a new :class:`BackendSpec` here, when the runtime speaks the OpenAI Chat
    Completions API (llama.cpp server, LM Studio, vLLM, ...), or
  * a new small adapter class in this package plus a spec whose ``kind``
    selects it, when the runtime has behaviour the generic API cannot express
    (Ollama's context discovery is the existing example).

Nothing outside this package branches on a backend name: the pipeline asks
the :class:`~summarizer.engine.base.Engine` what it supports and negotiates
from that. This file deliberately has no imports from the rest of the
application, so it can be used by configuration loading without an import
cycle.

Only loopback defaults are declared here. No cloud provider is described and
no remote URL is introduced anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "BackendSpec",
    "BACKEND_SPECS",
    "resolve_backend_spec",
    "canonical_backend_name",
    "known_backends",
    "backend_choices",
    "default_endpoint_for_backend",
]

# Adapter families. ``openai-compatible`` is served by one generic engine;
# ``ollama`` additionally uses Ollama's native metadata API.
KIND_OPENAI_COMPATIBLE = "openai-compatible"
KIND_OLLAMA = "ollama"


@dataclass(frozen=True)
class BackendSpec:
    """Static description of a local backend.

    ``structured_json``/``reasoning_control`` are *declared* capability hints
    (three-valued: True advertised, False refused, None unknown). The live
    engine may refine them; they are only a starting point so the pipeline
    never has to guess from a backend or model name.
    """

    name: str
    kind: str = KIND_OPENAI_COMPATIBLE
    aliases: tuple[str, ...] = ()
    description: str = ""
    # Loopback endpoint used only when the user never configured one; empty
    # means "keep whatever the built-in default is".
    default_endpoint: str = ""
    streaming: bool = True
    structured_json: bool | None = None
    model_listing: bool = True
    reasoning_control: bool | None = None


BACKEND_SPECS: tuple[BackendSpec, ...] = (
    BackendSpec(
        name="ollama",
        kind=KIND_OLLAMA,
        aliases=(),
        description="Ollama, including native model/context discovery",
        default_endpoint="http://localhost:11434",
        structured_json=None,
        reasoning_control=True,
    ),
    BackendSpec(
        name="openai-compatible",
        kind=KIND_OPENAI_COMPATIBLE,
        aliases=("openai",),
        description="Generic local server speaking the OpenAI Chat Completions API",
        # No canonical port: keep the configured endpoint. Ollama itself
        # serves this API on 11434, which is the built-in default.
        default_endpoint="",
        structured_json=None,
        reasoning_control=None,
    ),
    BackendSpec(
        name="llama.cpp",
        kind=KIND_OPENAI_COMPATIBLE,
        aliases=("llama-cpp", "llamacpp", "llama_cpp"),
        description="llama.cpp server (OpenAI-compatible endpoint)",
        default_endpoint="http://localhost:8080",
        structured_json=None,
        reasoning_control=None,
    ),
    BackendSpec(
        name="lmstudio",
        kind=KIND_OPENAI_COMPATIBLE,
        aliases=("lm-studio", "lm_studio"),
        description="LM Studio local server (OpenAI-compatible endpoint)",
        default_endpoint="http://localhost:1234",
        structured_json=None,
        reasoning_control=None,
    ),
)


def _alias_map() -> dict[str, BackendSpec]:
    mapping: dict[str, BackendSpec] = {}
    for spec in BACKEND_SPECS:
        mapping[spec.name] = spec
        for alias in spec.aliases:
            mapping[alias] = spec
    return mapping


def resolve_backend_spec(name: str | None) -> BackendSpec | None:
    """Return the spec for a configured backend name or alias (None if unknown)."""
    if not name:
        return None
    return _alias_map().get(name.strip().lower())


def canonical_backend_name(name: str | None) -> str | None:
    """Normalise an alias to its canonical backend name."""
    spec = resolve_backend_spec(name)
    return spec.name if spec else None


def known_backends() -> tuple[str, ...]:
    """Canonical backend names, for errors and documentation."""
    return tuple(spec.name for spec in BACKEND_SPECS)


def backend_choices() -> tuple[str, ...]:
    """Canonical names plus aliases, for ``--help`` text."""
    choices: list[str] = []
    for spec in BACKEND_SPECS:
        choices.append(spec.name)
        choices.extend(spec.aliases)
    return tuple(choices)


def default_endpoint_for_backend(name: str | None) -> str | None:
    """Loopback default endpoint for a backend, when it declares one."""
    spec = resolve_backend_spec(name)
    if spec and spec.default_endpoint:
        return spec.default_endpoint
    return None
