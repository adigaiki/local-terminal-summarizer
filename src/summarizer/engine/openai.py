"""OpenAI-compatible local engine.

Talks to any server exposing the OpenAI Chat Completions API (Ollama's
`/v1` endpoint included). This is deliberately the *generic* engine: the
application core does not assume Ollama anywhere.
"""

from __future__ import annotations

import time
from typing import Any, Iterator

from summarizer.engine.base import Engine
from summarizer.engine.capabilities import EngineCapabilities
from summarizer.engine.client import HttpClient, HttpStatusError, HttpStatusErrorKinds
from summarizer.errors import EngineError, InputError, MalformedResponse, ModelNotFound
from summarizer.chunking.token import estimate_tokens
from summarizer.log import Diagnostics
from summarizer.config import EngineSettings

__all__ = ["OpenAICompatEngine"]

CHAT_PATH = "/v1/chat/completions"
MODELS_PATH = "/v1/models"

_RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


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
        diag: Diagnostics | None = None,
        http: HttpClient | None = None,
    ) -> None:
        self.backend = settings.backend
        self.endpoint = settings.endpoint
        self.model = settings.model
        self.timeout_seconds = float(settings.timeout_seconds)
        self.retries = max(0, settings.retries)
        self.temperature = float(settings.temperature)
        self.max_tokens = int(settings.max_tokens)
        self.reasoning_effort = settings.reasoning_effort
        self._diag = diag
        self._http = http or HttpClient(
            self.endpoint,
            timeout=self.timeout_seconds,
            diag=self._diag,
            max_response_bytes=settings.max_response_bytes,
        )
        self._capabilities: EngineCapabilities | None = None
        self._config_context_length = int(settings.context_length or 0)

    def _chat_path(self) -> str:
        return _endpoint_v1(self._http.base_path, CHAT_PATH)

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
                raise InputError(
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
        if temperature is not None:
            payload["temperature"] = temperature
        if json_object:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _reasoning_control_capability(self) -> bool | None:
        """Whether this backend supports the portable reasoning-effort field.

        Generic OpenAI-compatible servers are deliberately treated as
        unknown. Backends which support the field advertise it through this
        hook and the capability model instead of relying on a model name.
        """
        return None

    def _extract_text(self, data: dict[str, Any]) -> str | None:
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message")
            text = message.get("content") if isinstance(message, dict) else None
            if text is not None:
                return text
        return None

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
                delta = self._extract_stream_delta(event)
                if delta:
                    yield delta
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
                streaming=True,
                structured_json=None,  # never assume: probed at request time
                reasoning_control=self._reasoning_control_capability(),
                model_listing=True,
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
        capability = HttpStatusErrorKinds.capability_refused(exc)
        if capability is not None:
            return capability
        return EngineError(
            f"server returned {exc.status} from {exc.path}: {exc.body[:200]}",
            hint="check the endpoint, the model name, and the server logs",
        )
