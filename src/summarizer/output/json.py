"""Output formatters: plain, markdown, and validated JSON.

The JSON path is deliberately defensive. A local model can return prose, a
half-written object, or a code fence around its answer, and a summarizer that
prints that to stdout while exiting 0 would silently break every downstream
consumer of `--format json`. So we:

  1. request a structured object when the backend advertises support,
  2. validate the reply strictly (never trusting the model's formatting),
  3. make exactly one recovery attempt with an explicit correction,
  4. fail loudly (non-zero exit) rather than emit malformed JSON.

This module formats data. It does not know what an engine is: the caller
passes a small request callback, so the formatter stays independent of any
particular backend, model, or transport.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from summarizer.document.model import Document
from summarizer.errors import CapabilityNotSupported, MalformedResponse
from summarizer.log import Diagnostics

__all__ = [
    "format_plain",
    "format_markdown",
    "build_json_envelope",
    "dumps_json",
    "parse_json_strict",
    "generate_json",
]

# A request callback: given a prompt (and optionally a structured-output
# request), return the model's raw text reply.
JsonRequest = Callable[..., str]

_JSON_CORRECTION = (
    "\n\nIMPORTANT: respond with ONLY one valid JSON object. "
    "No prose before or after it. No markdown code fences."
)


def format_plain(text: str) -> str:
    return text.rstrip() + "\n"


def format_markdown(text: str) -> str:
    # The engine is asked for markdown by the profile; we only normalize the
    # trailing newline so piped output stays predictable.
    return text.rstrip() + "\n"


def build_json_envelope(
    summary: Any,
    *,
    document: Document,
    profile: str,
    engine_backend: str,
    model: str,
    endpoint: str,
    boundary: str | None,
    strategy: str,
    chunk_count: int,
    warnings: list[str],
    duration_seconds: float | None = None,
    chunk_provenance: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Structured JSON output. Every key is stable and documented."""
    envelope: dict[str, Any] = {
        "document": document.to_json(),
        "profile": profile,
        "engine": {"backend": engine_backend, "model": model, "endpoint": endpoint},
        "strategy": strategy,
        "chunks": chunk_count,
        "summary": summary,
        "warnings": warnings,
    }
    if chunk_provenance:
        envelope["chunk_provenance"] = chunk_provenance
    if boundary:
        envelope["prompt_boundary"] = boundary
    if duration_seconds is not None:
        envelope["duration_seconds"] = round(duration_seconds, 3)
    return envelope


def dumps_json(envelope: dict[str, Any]) -> str:
    """Serialize strictly. Never emits NaN/Infinity or invalid JSON."""
    try:
        text = json.dumps(envelope, indent=2, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise MalformedResponse(
            f"summary could not be encoded as valid JSON: {exc}",
            hint="the model returned a value that is not JSON-representable",
        ) from exc
    return text + "\n"


def parse_json_strict(text: str) -> tuple[bool, Any]:
    """Strictly parse a JSON string, tolerating surrounding code fences.

    Returns ``(True, value)`` on success or ``(False, stripped_text)`` when
    the text is not valid JSON. This function never raises, and it treats a
    literal ``null`` as a successful parse of the value ``None``.
    """
    candidate = text.strip()
    if candidate.startswith("```"):
        first = candidate.find("\n")
        last = candidate.rfind("```")
        if first != -1 and last > first:
            candidate = candidate[first + 1 : last].strip()
    try:
        value = json.loads(candidate)
        # Python accepts non-finite floats, but interoperable JSON does not.
        json.dumps(value, allow_nan=False)
        return True, value
    except (json.JSONDecodeError, ValueError, RecursionError):
        return False, candidate


def _request_once(
    request: JsonRequest,
    prompt: str,
    *,
    json_object: bool,
    diag: Diagnostics,
) -> tuple[bool, Any]:
    """One validated request. Returns ``(parsed_ok, value_or_raw_text)``.

    A backend that rejects the structured-output request is retried once
    without it, because that is a capability problem, not a model problem.
    """
    try:
        text = request(prompt, json_object=json_object)
    except CapabilityNotSupported:
        if not json_object:
            raise
        diag.warn("backend rejected the structured-output request; retrying without it")
        return _request_once(request, prompt, json_object=False, diag=diag)
    return parse_json_strict(text)


def _accept(value: Any, *, diag: Diagnostics) -> Any:
    """Accept any valid JSON, noting (but not rejecting) unusual shapes."""
    if not isinstance(value, (dict, list)):
        diag.warn(
            "structured output was valid JSON but not an object or array "
            f"({type(value).__name__}); emitting it as-is"
        )
    return value


def generate_json(
    request: JsonRequest,
    prompt: str,
    *,
    diag: Diagnostics,
    structured_supported: bool | None,
) -> Any:
    """Request JSON, validate it, recover once, and fail loudly if impossible.

    ``request`` is any callable with ``request(prompt, json_object=False)``
    returning the model's raw text: this module never imports an engine.

    Raises :class:`MalformedResponse` (a non-zero exit) when the model cannot
    produce valid JSON, so a caller never sees malformed JSON alongside
    success.
    """
    ok, value = _request_once(
        request, prompt, json_object=structured_supported is not False, diag=diag
    )
    if ok:
        return _accept(value, diag=diag)

    diag.warn("model returned invalid JSON; retrying once with a correction instruction")
    ok, value = _request_once(
        request, prompt + _JSON_CORRECTION, json_object=False, diag=diag
    )
    if not ok:
        raise MalformedResponse(
            "model did not return valid JSON even after a correction retry",
            hint="the model may not support reliable structured output; "
                 "try --format markdown, or a different model",
        )
    return _accept(value, diag=diag)
