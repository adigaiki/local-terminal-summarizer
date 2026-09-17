"""Chunking strategies and context budgeting.

Every request is planned against an explicit budget so a prompt can never
silently overflow the model's context window. The pipeline decides, per
request:

    * an effective context window (discovered from the backend, configured by
      the user, or a documented conservative fallback)
    * how much of it the trusted material already occupies (preamble, profile
      instructions, and any user-supplied --context)
    * the largest single chunk that may be sent
    * whether map-reduce aggregation is required, and how many chunks that
      implies

Strict mode refuses to run when the document cannot fit, instead of quietly
falling back to a lossy multi-chunk summary.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from summarizer.chunking.budget import (
    PROMPT_OVERHEAD_TOKENS,
    ContextBudget,
    compute_input_budget,
)
from summarizer.chunking.splitter import (
    BOUNDARY_WHOLE,
    Chunk,
    split_document,
)
from summarizer.chunking.token import estimate_tokens
from summarizer.config import ChunkingSettings
from summarizer.engine.capabilities import CONTEXT_SOURCE_CONFIG, EngineCapabilities
from summarizer.errors import InputError
from summarizer.profiles import Profile

__all__ = [
    "Chunk",
    "chunk_document",
    "ContextBudget",
    "budget_for",
    "prompt_token_overhead",
    "chunking_decision",
    "PROMPT_OVERHEAD_TOKENS",
]


def prompt_token_overhead(profile: Profile) -> int:
    """Tokens the trusted instructions occupy before any document content."""
    return PROMPT_OVERHEAD_TOKENS + estimate_tokens(profile.text)


def budget_for(
    capabilities: EngineCapabilities,
    settings: ChunkingSettings,
    profile: Profile,
    *,
    config_context_length: int = 0,
    context_file_tokens: int = 0,
    max_output_tokens: int = 0,
) -> ContextBudget:
    """Compute the token budget for one generation request.

    Pure arithmetic lives in :mod:`summarizer.chunking.budget`; this wrapper
    only resolves which context window applies and how big the trusted
    material is.
    """
    if config_context_length:
        context = int(config_context_length)
        source = CONTEXT_SOURCE_CONFIG
    else:
        context = capabilities.effective_context_length
        source = capabilities.effective_context_source
    return compute_input_budget(
        context_length=context,
        prompt_tokens=prompt_token_overhead(profile),
        max_tokens_per_chunk=settings.max_tokens_per_chunk,
        reserved_output_tokens=max(settings.reserve_output_tokens, max_output_tokens),
        context_file_tokens=context_file_tokens,
        context_source=source,
    )


@dataclass(frozen=True)
class ChunkingDecision:
    """The plan for one document: how it is split and how it is aggregated."""

    strategy: str
    unit: str  # "tokens" | "chars"
    chunk_count: int
    max_chunk: int
    budget: ContextBudget | None
    single_shot: bool
    reason: str
    max_chunks: int = 0  # configured ceiling, 0 == unlimited

    def describe(self) -> str:
        parts = [
            f"chunking: {self.strategy} ({self.unit}); "
            f"{self.chunk_count} chunk(s), max ~{self.max_chunk} {self.unit} each"
        ]
        if self.budget is not None:
            parts.append(self.budget.describe())
        if self.max_chunks:
            parts.append(f"chunk ceiling: {self.max_chunks}")
        return "; ".join(parts)


def chunking_decision(
    content: str,
    *,
    capabilities: EngineCapabilities,
    settings: ChunkingSettings,
    profile: Profile,
    config_context_length: int,
    explicit_unit: str | None = None,
    strict: bool = False,
    context_file_tokens: int = 0,
    max_chunks: int | None = None,
    max_output_tokens: int = 0,
) -> ChunkingDecision:
    """Choose a strategy for ``content`` and prove it fits the budget.

    ``explicit_unit`` is "auto" (default), "tokens", or "chars". ``strict``
    turns an otherwise-silent chunking decision into a hard error.
    """
    requested = (explicit_unit or "auto").strip().lower()
    unit = "tokens"
    if requested == "chars":
        unit = "chars"
    elif requested not in ("auto", "tokens"):
        raise InputError(
            f"invalid chunk strategy {requested!r}",
            hint="expected one of: auto, tokens, chars",
        )

    ceiling = int(max_chunks if max_chunks is not None else settings.max_chunks or 0)
    budget = budget_for(
        capabilities,
        settings,
        profile,
        config_context_length=config_context_length,
        context_file_tokens=context_file_tokens,
        max_output_tokens=max_output_tokens,
    )
    if budget.max_chunk_tokens < 1:
        raise InputError(
            "the context window is smaller than the prompt and reserved output space",
            hint="raise the model context window or lower reserve_output_tokens",
        )

    if unit == "chars":
        # Characters are the splitting unit, but the *token* budget is what
        # protects the context window (the chars/4 conversion is not a safe
        # inverse of the estimator, which also counts words). Both limits
        # must hold, including in strict mode.
        doc_chars = max(1, len(content))
        doc_units = max(1, estimate_tokens(content))
        max_chunk = min(budget.max_chunk_tokens * 4, settings.max_tokens_per_chunk * 4)
        single = doc_chars <= max_chunk and doc_units <= budget.max_chunk_tokens
        chunk_count = 1 if single else max(
            math.ceil(doc_chars / max(1, max_chunk)),
            math.ceil(doc_units / max(1, budget.max_chunk_tokens)),
        )
    else:
        doc_units = estimate_tokens(content)
        max_chunk = budget.max_chunk_tokens
        single = doc_units <= max_chunk
        chunk_count = 1 if single else math.ceil(doc_units / max(1, max_chunk))

    if strict and not single:
        raise _strict_error(budget, doc_units, unit)

    if ceiling and chunk_count > ceiling:
        raise InputError(
            f"document needs {chunk_count} chunks, exceeding the configured "
            f"maximum of {ceiling}",
            hint="raise `[chunking] max_chunks` in config (or --max-chunks), "
                 "split the input, or use a model with a larger context window",
        )

    reason = (
        "fits in context"
        if single
        else (
            f"document exceeds usable context (need ~{doc_units} {unit}, "
            f"usable {max_chunk} per chunk)"
        )
    )
    return ChunkingDecision(
        strategy="auto" if requested == "auto" else requested,
        unit=unit,
        chunk_count=chunk_count,
        max_chunk=max_chunk,
        budget=budget,
        single_shot=single,
        reason=reason,
        max_chunks=ceiling,
    )


def _strict_error(budget: ContextBudget, doc_units: int, unit: str) -> InputError:
    """A clear refusal: strict mode never silently degrades to map-reduce."""
    if budget.usable_input_tokens <= 0:
        return InputError(
            "strict mode: the context window is smaller than the reserved "
            "prompt+output space, so not even one chunk fits",
            hint="raise the model context window or lower reserve_output_tokens; "
                 "or drop --strict to allow map-reduce chunking",
        )
    return InputError(
        f"strict mode: document ({doc_units} {unit}) exceeds the usable context "
        f"window of {budget.usable_input_tokens} tokens",
        hint="drop --strict to summarize via map-reduce, or use a model with a "
             "larger context window",
    )


def chunk_document(
    content: str,
    *,
    decision: ChunkingDecision,
    settings: ChunkingSettings | None = None,
    source: str | None = None,
) -> list[Chunk]:
    """Produce chunks for a previously computed decision.

    ``source`` is recorded on every chunk so aggregated output can report where
    each piece came from.
    """
    settings = settings or ChunkingSettings()
    if decision.single_shot:
        return [
            Chunk(
                index=0,
                text=content,
                token_estimate=estimate_tokens(content),
                char_count=len(content),
                start_char=0,
                end_char=len(content),
                overlap_chars=0,
                boundary=BOUNDARY_WHOLE,
                source=source,
            )
        ]
    max_units = decision.max_chunk
    overlap = settings.overlap_tokens * (4 if decision.unit == "chars" else 1)
    return split_document(
        content,
        max_units=max_units,
        overlap_units=overlap,
        unit=decision.unit,
        source=source,
        max_chunks=decision.max_chunks or 256,
        # Character-sized chunks must also respect the token budget that
        # actually protects the context window (chars/4 is not a safe
        # inverse of the estimator when words are short).
        max_tokens=(
            decision.budget.max_chunk_tokens
            if decision.unit == "chars" and decision.budget is not None
            else None
        ),
    )


def jaccard_overlap_ratio(a: str, b: str) -> float:
    """Token-overlap measure used in tests to assert overlap behavior."""
    sa, sb = set(a.split()), set(b.split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)
