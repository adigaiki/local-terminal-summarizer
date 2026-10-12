"""Ollama backend.

Ollama is treated as a *kind* of OpenAI-compatible server, plus a couple of
Ollama-specific conveniences that come from its own local HTTP API:

  * native model listing via ``/api/tags``
  * context-window discovery via ``/api/show`` (and, as a fallback, the
    per-model metadata that ``/api/tags`` already returns)

Nothing here is specific to a particular model family: whatever the installed
Ollama reports for the configured model is what we use. Nothing in the core
pipeline assumes Ollama; swap backends in config.
"""

from __future__ import annotations

import re
from typing import Any

from summarizer.config import EngineSettings
from summarizer.engine.backends import BackendSpec, resolve_backend_spec
from summarizer.engine.capabilities import (
    CONTEXT_SOURCE_CONFIG,
    CONTEXT_SOURCE_FALLBACK,
    CONTEXT_SOURCE_SERVER,
    CONTEXT_SOURCE_TRAINED,
    DEFAULT_CONTEXT_LENGTH,
    EngineCapabilities,
)
from summarizer.engine.client import HttpClient
from summarizer.engine.openai import OpenAICompatEngine
from summarizer.errors import MalformedResponse
from summarizer.log import Diagnostics

__all__ = ["OllamaEngine"]

TAGS_PATH = "/api/tags"
SHOW_PATH = "/api/show"
PS_PATH = "/api/ps"
GENERATE_PATH = "/api/generate"

# Keys Ollama uses for the trained context window inside /api/show model_info.
_CONTEXT_KEY_SUFFIXES = (".context_length", "context_length")


def _strip_latest(name: str) -> str:
    return name[: -len(":latest")] if name.endswith(":latest") else name


class OllamaEngine(OpenAICompatEngine):
    """Engine that talks to an Ollama server's OpenAI-compatible API."""

    def __init__(
        self,
        settings: EngineSettings,
        *,
        spec: BackendSpec | None = None,
        diag: Diagnostics | None = None,
        http: HttpClient | None = None,
    ) -> None:
        super().__init__(settings, spec=spec or resolve_backend_spec("ollama"), diag=diag, http=http)
        # The OpenAI-compatible chat path is still served under /v1/... by
        # Ollama; the adapter label is always the canonical one.
        self.backend = "ollama"

    # -- model listing -------------------------------------------------------

    def _tags(self) -> list[dict[str, Any]]:
        data = self._http.get_json(TAGS_PATH, timeout=min(self.timeout_seconds, 15))
        raw = data.get("models") if isinstance(data, dict) else None
        if not isinstance(raw, list):
            raise MalformedResponse(
                f"expected a model list from {TAGS_PATH}, got {data!r}"
            )
        return [item for item in raw if isinstance(item, dict)]

    def list_models(self) -> list[str]:
        models: list[str] = []
        for item in self._tags():
            name = item.get("name")
            if name:
                models.append(_strip_latest(str(name)))
        return models

    def _tags_entry(self, model: str) -> dict[str, Any] | None:
        """Find the entry for ``model`` in /api/tags, tolerating ``:latest``."""
        requested = _strip_latest(model)
        entries = self._tags()
        for item in entries:
            if _strip_latest(str(item.get("name", ""))) == requested:
                return item
        if ":" not in requested:
            # A tagless configured name conventionally resolves to a tag of
            # the same model family; only used for metadata lookups.
            for item in entries:
                name = str(item.get("name", ""))
                if name.split(":", 1)[0] == requested:
                    return item
        return None

    # -- context discovery ---------------------------------------------------

    def _configured_context_length(self) -> int | None:
        value = int(self._config_context_length or 0)
        return value if value > 0 else None

    @staticmethod
    def _context_from_show(info: Any) -> tuple[int | None, str | None, str | None]:
        """Extract a context length from an /api/show response.

        Returns ``(length, source, detail)``. A ``parameters.num_ctx`` is the
        model's own configured serving window; a ``model_info`` value is only
        the *trained maximum*, which the server may serve far below, so it gets
        :data:`CONTEXT_SOURCE_TRAINED` rather than ``server``.
        """
        if not isinstance(info, dict):
            return None, None, None
        params = info.get("parameters")
        if isinstance(params, str):
            match = re.search(r"(?m)^\s*num_ctx\s+(\d+)\s*$", params)
            params = {"num_ctx": int(match.group(1))} if match else {}
        if isinstance(params, dict):
            num_ctx = params.get("num_ctx")
            if type(num_ctx) is int and num_ctx > 0:
                return num_ctx, CONTEXT_SOURCE_SERVER, f"{SHOW_PATH} (parameters.num_ctx)"
        model_info = info.get("model_info")
        if isinstance(model_info, dict):
            for suffix in _CONTEXT_KEY_SUFFIXES:
                for key, value in model_info.items():
                    if str(key).endswith(suffix) and type(value) is int and value > 0:
                        return (
                            value,
                            CONTEXT_SOURCE_TRAINED,
                            f"{SHOW_PATH} ({key}; trained maximum, model not loaded)",
                        )
        return None, None, None

    def _context_from_tags(self) -> tuple[int | None, str | None]:
        """Fallback: per-model metadata already present in /api/tags."""
        try:
            entry = self._tags_entry(self.model)
        except Exception:
            return None, None
        if not entry:
            return None, None
        details = entry.get("details")
        if not isinstance(details, dict):
            return None, None
        value = details.get("context_length")
        if isinstance(value, int) and value > 0:
            return value, f"{TAGS_PATH} (details.context_length)"
        return None, None

    def _context_from_ps(self) -> tuple[int | None, str | None]:
        """Runtime context window for the *loaded* model, via /api/ps.

        The serving window (set by Ollama's `num_ctx`) can be far smaller than
        the trained maximum in `model_info`; the serving window is the one
        requests must actually fit, so it wins when the model is loaded.
        """
        try:
            data = self._http.get_json(PS_PATH, timeout=min(self.timeout_seconds, 15))
        except Exception:
            return None, None
        raw = data.get("models") if isinstance(data, dict) else None
        if not isinstance(raw, list):
            return None, None
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = _strip_latest(str(item.get("name", "")))
            if name != _strip_latest(self.model):
                continue
            value = item.get("context_length")
            if isinstance(value, int) and value > 0:
                return value, f"{PS_PATH} (loaded runtime context)"
        return None, None

    def _detect_context_length(self) -> tuple[int | None, str | None, str | None]:
        """Return ``(context_length, source, note)`` for the configured model."""
        configured = self._configured_context_length()
        if configured:
            return configured, CONTEXT_SOURCE_CONFIG, None

        # Prefer the serving window of the loaded model: planning against the
        # trained maximum can overflow what the server actually provides.
        loaded, ps_note = self._context_from_ps()
        if loaded:
            return loaded, CONTEXT_SOURCE_SERVER, f"context discovered via {ps_note}"
        shown: int | None = None
        note: str | None = ps_note
        try:
            info = self._http.post_json(
                SHOW_PATH, {"model": self.model},
                timeout=min(self.timeout_seconds, 15),
            )
            shown, shown_source, detail = self._context_from_show(info)
            if shown:
                return shown, (shown_source or CONTEXT_SOURCE_SERVER), f"context discovered via {detail}"
            note = f"{SHOW_PATH} reported no context length for {self.model!r}"
        except Exception as exc:  # unreachable model, older server, bad reply
            note = f"{SHOW_PATH} unavailable ({type(exc).__name__}); context unknown"

        tagged, detail = self._context_from_tags()
        if tagged:
            return tagged, CONTEXT_SOURCE_SERVER, f"context discovered via {detail}"
        return None, None, note

    def capabilities(self) -> EngineCapabilities:
        if self._capabilities is None:
            length, source, note = self._detect_context_length()
            caps = EngineCapabilities(
                backend=self.backend,
                streaming=True,
                structured_json=None,  # never assume; probed at request time
                reasoning_control=True,
                model_listing=True,
                context_length=length,
                context_source=source,
            )
            if note:
                caps = caps.with_notes(note)
            self._capabilities = caps
        return self._capabilities

    def prepare(self) -> None:
        """Warm the model up so the *served* window can be read before planning.

        Ollama serves a window that can be far below the model's trained
        maximum, and it only appears in ``/api/ps`` once the model is loaded.
        When discovery has only the trained maximum (or nothing), send one tiny
        request to load the model, then re-read ``/api/ps``. If that fails,
        fall back to a conservative cap instead of the trained maximum, so an
        oversized single-shot prompt is never planned. ``[engine]
        context_length`` always wins. ``doctor`` never calls this.
        """
        if self._config_context_length:
            return
        caps = self.capabilities()
        if caps.context_source not in (CONTEXT_SOURCE_TRAINED, None):
            return  # already a real served window (or explicitly configured)

        warmed = False
        try:
            self._http.post_json(
                GENERATE_PATH,
                {
                    "model": self.model,
                    "prompt": "",
                    "stream": False,
                    "options": {"num_predict": 1},
                },
                timeout=self.timeout_seconds,
            )
            warmed = True
        except Exception as exc:
            if self._diag:
                self._diag.verbose_warn(
                    f"model warm-up failed ({type(exc).__name__}: {exc})"
                )

        loaded, ps_note = self._context_from_ps()
        if loaded:
            self._capabilities = caps.with_context_length(
                loaded, source=CONTEXT_SOURCE_SERVER
            ).with_notes(f"context discovered via {ps_note}")
            return

        conservative = min(
            caps.context_length or DEFAULT_CONTEXT_LENGTH, DEFAULT_CONTEXT_LENGTH
        )
        self._capabilities = EngineCapabilities(
            backend=self.backend,
            streaming=True,
            structured_json=None,
            reasoning_control=True,
            model_listing=True,
            context_length=conservative,
            context_source=CONTEXT_SOURCE_FALLBACK,
        )
        if self._diag:
            self._diag.warn(
                f"could not read the served context window for {self.model!r} "
                f"(warm-up {'failed' if not warmed else 'did not load it'}); "
                f"planning against a conservative {conservative}-token cap instead "
                "of the trained maximum. Set `[engine] context_length` or check "
                "`ollama ps`."
            )
