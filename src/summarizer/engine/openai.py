"""OpenAI-compatible local engine.

Talks to any server exposing the OpenAI Chat Completions API (Ollama's
`/v1` endpoint included). This is deliberately the *generic* engine: the
application core does not assume Ollama anywhere.
"""

from __future__ import annotations

import time
from typing import Any, Iterator

from summarizer.engine.backends import BackendSpec
from summarizer.engine.base import Engine
from summarizer.engine.capabilities import (
    CONTEXT_SOURCE_CONFIG,
    CONTEXT_SOURCE_SERVER,
    EngineCapabilities,
)
from summarizer.engine.client import HttpClient, HttpStatusError, HttpStatusErrorKinds
from summarizer.errors import ContextOverflow, EngineError, MalformedResponse, ModelNotFound, TruncationDetected
from summarizer.chunking.token import estimate_tokens
from summarizer.log import Diagnostics
from summarizer.config import EngineSettings

__all__ = ["OpenAICompatEngine"]

CHAT_PATH = "/v1/chat/completions"
MODELS_PATH = "/v1/models"

_RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}

# Truncation detection uses Ollama's measured signature: a truncated prompt is
# processed as exactly ``window/2 + 2`` prompt tokens (measured on Ollama
# 0.35.0: 2050 of 4096, 8194 of 16384, 1026 of 2048, 16386 of 32768). Two
# tiers. The hard tier (our estimate far above what the server processed)
# retries with smaller chunks; the soft tier (fingerprint match but a low
# estimate) only notes it, because a low estimate means truncation cannot be
# distinguished from a genuine count.
_TRUNCATION_MARGIN = 2
_TRUNCATION_RATIO = 1.25


def _truncation_tier(
    *, est: int, reported: int, window: int | None, window_source: str | None
) -> str | None:
    """Return ``"hard"``, ``"soft"`` or ``None`` for one call's prompt tokens.

    The fingerprint is only meaningful against a trustworthy window, so it is
    applied only when the window came from the server or from
    ``[engine] context_length``.
    """
    if not window or window <= 0 or reported <= 0 or est <= 0:
        return None
    if window_source not in (CONTEXT_SOURCE_SERVER, CONTEXT_SOURCE_CONFIG):
        return None
    if abs(reported - (window // 2 + 2)) > _TRUNCATION_MARGIN:
        return None
    return "hard" if est > _TRUNCATION_RATIO * reported else "soft"


def _endpoint_v1(base_path: str, path: str) -> str:
    """Join a possibly-already-versioned base path with an API path."""
    if base_path.endswith("/v1"):
        return path[len("/v1"):] or "/"
    return path


class OpenAICompatEngine(Engine):
    """Generic engine over the OpenAI-compatible chat API."""

    def __init__(
        self,
        settings: EngineSettings,
        *,
        spec: BackendSpec | None = None,
        diag: Diagnostics | None = None,
        http: HttpClient | None = None,
    ) -> None:
        # Resolve the adapter description from the configured backend when the
        # factory did not pass one explicitly (direct construction in tests).
        # The generic engine deliberately does *not* infer a spec: without one
        # it stays backend-agnostic (unknown reasoning control), so a caller
        # constructing it directly gets generic behaviour.
        self.spec = spec
        self.backend = self.spec.name if self.spec else settings.backend
        self.endpoint = settings.endpoint
        self.model = settings.model
        self.timeout_seconds = float(settings.timeout_seconds)
        self.retries = max(0, settings.retries)
        self.temperature = float(settings.temperature)
        self.max_tokens = int(settings.max_tokens)
        self.reasoning_effort = settings.reasoning_effort
        self._diag = diag
        self._reasoning_warned = False
        self._http = http or HttpClient(
            self.endpoint,
            timeout=self.timeout_seconds,
            diag=self._diag,
            max_response_bytes=settings.max_response_bytes,
            first_token_timeout=settings.first_token_timeout_seconds,
        )
        self._capabilities: EngineCapabilities | None = None
        self._config_context_length = int(settings.context_length or 0)
        # Truncation handling: "raise" (non-streaming, so the pipeline can
        # retry with smaller chunks) or "warn" (the run accepts a truncated
        # result and records it).
        self.truncation_action = "raise"
        self.last_truncation: str | None = None
        self._soft_noted = False
        # Only backends whose usage counts are trustworthy are checked; a
        # generic OpenAI-compatible server may report counts the estimator
        # cannot be compared against. Tests may override this directly.
        self.detect_truncation = bool(getattr(spec, "usage_reporting", False))

    def set_truncation_action(self, action: str) -> None:
        self.truncation_action = action

    def _chat_path(self) -> str:
        return _endpoint_v1(self._http.base_path, CHAT_PATH)

    def set_cancel(self, cancel: object | None) -> None:
        """Let the HTTP layer observe cooperative cancellation."""
        self._http.cancel = cancel

    def _models_path(self) -> str:
        return _endpoint_v1(self._http.base_path, MODELS_PATH)

    def _payload(
        self,
        prompt: str,
        *,
        stream: bool,
        json_object: bool = False,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        # Last-resort invariant: the rendered prompt plus the reserved output
        # must fit the planned window. Estimated, never silently exceeded;
        # a genuine overflow is a loud configuration error instead of a
        # truncated or dropped response.
        estimated = estimate_tokens(prompt) + max(0, self.max_tokens)
        if self._capabilities is not None:
            length = self._capabilities.context_length
            if length and estimated > length:
                raise ContextOverflow(
                    f"prompt of ~{estimate_tokens(prompt)} tokens plus "
                    f"{self.max_tokens} reserved output tokens exceeds the "
                    f"{length}-token context window",
                    hint="lower reserve_output_tokens/max_tokens, or use a "
                         "model with a larger context window",
                )
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": stream,
            "max_tokens": self.max_tokens,
        }
        if self._reasoning_control_capability() is True:
            payload["reasoning_effort"] = self.reasoning_effort
        elif self.reasoning_effort not in ("none", "") and not self._reasoning_warned:
            # Documented fallback: a requested reasoning budget that the
            # backend does not advertise is omitted rather than risking a
            # request the server rejects. Reported once, never silently.
            self._reasoning_warned = True
            if self._diag:
                self._diag.verbose_warn(
                    f"backend {self.backend!r} does not advertise reasoning "
                    f"control; omitting reasoning_effort={self.reasoning_effort!r}"
                )
        if temperature is not None:
            payload["temperature"] = temperature
        if json_object:
            payload["response_format"] = {"type": "json_object"}
        if stream and self.spec is not None and getattr(self.spec, "stream_usage", False):
            # Ask for a final usage chunk so truncation can be detected while
            # streaming too. Only backends that declare support receive it.
            payload["stream_options"] = {"include_usage": True}
        return payload

    def _reasoning_control_capability(self) -> bool | None:
        """Whether this backend supports the portable reasoning-effort field.

        Declared statically by the adapter's :class:`BackendSpec`; generic
        OpenAI-compatible servers are deliberately treated as unknown. No
        model-family name is ever inspected.
        """
        if self.spec is not None:
            return self.spec.reasoning_control
        return None

    def _check_truncation(self, prompt: str, *, streaming: bool) -> None:
        """Detect a silently truncated prompt from the server's own counts.

        Uses Ollama's measured truncation signature (``window/2 + 2`` prompt
        tokens) against a trustworthy window:

        * **hard** -- the estimate is far above what the server processed
          (``est > 1.25 * reported``): raise :class:`TruncationDetected` so the
          pipeline retries with smaller chunks, except while streaming (output
          already emitted) or when ``truncation_action == "warn"``, where it
          warns and records the truncation instead.
        * **soft** -- the signature matches but the estimate is low, so
          truncation cannot be told from a genuine count: note it once on
          stderr, with no retry and no flag.
        """
        usage = self.last_usage
        if not self.detect_truncation or not usage:
            return
        reported = usage.get("prompt_tokens")
        if not isinstance(reported, int) or reported <= 0:
            return
        est = estimate_tokens(prompt)
        caps = self._capabilities
        window = caps.context_length if caps else None
        source = caps.context_source if caps else None
        tier = _truncation_tier(
            est=est, reported=reported, window=window, window_source=source
        )
        if tier is None:
            self.last_truncation = None  # a clean call clears a stale flag
            return
        if tier == "soft":
            # Fingerprint match, but our estimate is not high enough to call it
            # a truncation: note once; no retry, no flag, no exit change.
            self.last_truncation = None
            if not self._soft_noted and self._diag:
                self._soft_noted = True
                self._diag.info(
                    "token count matches Ollama's truncation signature; the "
                    "summary may be missing the beginning"
                )
            return
        self.last_truncation = "truncation-signature"
        message = (
            f"the backend processed {reported} prompt tokens for a prompt of "
            f"~{est} estimated tokens, matching Ollama's truncation signature "
            f"(window/2 + 2 = {window // 2 + 2}); the summary may be missing the "
            "beginning of the document"
        )
        hint = (
            "lower `[chunking] max_tokens_per_chunk`, raise the model's context "
            "window, or set `[engine] context_length`"
        )
        if not streaming and self.truncation_action == "raise":
            raise TruncationDetected(message, hint=hint, ratio=reported / est)
        if self._diag:
            self._diag.warn(message)

    def _extract_text(self, data: dict[str, Any]) -> str | None:
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message")
            text = message.get("content") if isinstance(message, dict) else None
            if text is not None:
                return text
        return None

    @staticmethod
    def _extract_usage(data: dict[str, Any]) -> dict[str, int] | None:
        """Best-effort token usage from an OpenAI-compatible response."""
        usage = data.get("usage")
        if not isinstance(usage, dict):
            return None
        result: dict[str, int] = {}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = usage.get(key)
            if isinstance(value, int):
                result[key] = value
        return result or None

    def _extract_stream_delta(self, data: dict[str, Any]) -> str | None:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            return None
        choice = choices[0]
        delta = choice.get("delta")
        if isinstance(delta, dict) and delta.get("content"):
            return delta["content"]
        message = choice.get("message")
        if isinstance(message, dict) and message.get("content"):
            return message["content"]
        text = choice.get("text")
        return text if text else None

    def _run_with_retries(self, operation: str, fn):
        """Run a network operation with retries and clear error mapping.

        Model-missing errors are never retried (the server is up; the model
        name is wrong). Transport and 5xx-ish failures are retried with a
        short backoff.
        """
        attempts = max(1, self.retries + 1)
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return fn()
            except ModelNotFound:
                raise
            except (EngineError, OSError) as exc:
                if attempt >= attempts:
                    raise
                wait = 0.5 * (2 ** (attempt - 1))
                if self._diag:
                    self._diag.progress(
                        f"{operation} failed ({type(exc).__name__}: {getattr(exc, 'message', exc)}); "
                        f"retrying in {wait:.1f}s ({attempt}/{attempts})..."
                    )
                time.sleep(wait)
                last_error = exc
        assert last_error is not None
        raise last_error

    # -- Engine interface ----------------------------------------------------

    def generate(
        self,
        *,
        prompt: str,
        json_object: bool = False,
        temperature: float | None = None,
    ) -> str:
        payload = self._payload(
            prompt, stream=False, json_object=json_object,
            temperature=self.temperature if temperature is None else temperature,
        )

        def _call() -> str:
            try:
                data = self._http.post_json(self._chat_path(), payload)
            except HttpStatusError as exc:
                raise self._classify_status_error(exc) from exc
            if not isinstance(data, dict):
                raise MalformedResponse(f"endpoint returned unexpected payload: {data!r}")
            text = self._extract_text(data)
            if text is None:
                raise MalformedResponse(
                    f"response had no usable text (keys: {sorted(data.keys())})",
                    hint="the endpoint may not be an OpenAI-compatible chat API",
                )
            self.last_usage = self._extract_usage(data)
            self._check_truncation(prompt, streaming=False)
            return text

        return self._run_with_retries("generation", _call)

    def stream(
        self,
        *,
        prompt: str,
        temperature: float | None = None,
    ) -> Iterator[str]:
        payload = self._payload(
            prompt, stream=True,
            temperature=self.temperature if temperature is None else temperature,
        )
        try:
            for event in self._http.stream_sse(self._chat_path(), payload):
                if not isinstance(event, dict):
                    continue
                usage = self._extract_usage(event)
                if usage:
                    self.last_usage = usage
                delta = self._extract_stream_delta(event)
                if delta:
                    yield delta
            self._check_truncation(prompt, streaming=True)
        except HttpStatusError as exc:
            raise self._classify_status_error(exc) from exc

    def health(self) -> bool:
        try:
            self._http.get_json(self._models_path(), timeout=min(self.timeout_seconds, 10))
            return True
        except HttpStatusError as exc:
            return False
        except EngineError:
            return False

    def list_models(self) -> list[str]:
        try:
            data = self._http.get_json(self._models_path(), timeout=min(self.timeout_seconds, 15))
        except HttpStatusError as exc:
            raise self._classify_status_error(exc) from exc
        raw = data.get("data") if isinstance(data, dict) else None
        if not isinstance(raw, list):
            raise MalformedResponse(
                f"expected a model list from {self._models_path()}, got {data!r}"
            )
        models: list[str] = []
        for item in raw:
            if isinstance(item, dict) and item.get("id"):
                models.append(str(item["id"]))
        return models

    def capabilities(self) -> EngineCapabilities:
        if self._capabilities is None:
            caps = EngineCapabilities(
                backend=self.backend,
                streaming=bool(self.spec.streaming) if self.spec else True,
                # Never assume structured output: it is probed at request time.
                structured_json=None,
                reasoning_control=self._reasoning_control_capability(),
                model_listing=bool(self.spec.model_listing) if self.spec else True,
            )
            if self._config_context_length:
                caps = caps.with_context_length(self._config_context_length)
            self._capabilities = caps
        return self._capabilities

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _classify_status_error(exc: HttpStatusError) -> EngineError:
        model_missing = HttpStatusErrorKinds.model_missing(exc)
        if model_missing is not None:
            return model_missing
        overflow = HttpStatusErrorKinds.context_overflow(exc)
        if overflow is not None:
            return overflow
        capability = HttpStatusErrorKinds.capability_refused(exc)
        if capability is not None:
            return capability
        return EngineError(
            f"server returned {exc.status} from {exc.path}: {exc.body[:200]}",
            hint="check the endpoint, the model name, and the server logs",
        )
