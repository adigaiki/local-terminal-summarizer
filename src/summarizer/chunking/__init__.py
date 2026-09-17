"""Chunking: token estimation, context budgeting, boundary-aware splitting."""

from summarizer.chunking.budget import (
    PROMPT_OVERHEAD_TOKENS,
    ContextBudget,
    compute_input_budget,
)
from summarizer.chunking.splitter import (
    BOUNDARY_HARD,
    BOUNDARY_ORDER,
    BOUNDARY_PARAGRAPH,
    BOUNDARY_SENTENCE,
    BOUNDARY_WHOLE,
    BOUNDARY_WORD,
    Chunk,
    split_document,
)
from summarizer.chunking.strategies import (
    ChunkingDecision,
    budget_for,
    chunk_document,
    chunking_decision,
    prompt_token_overhead,
)
from summarizer.chunking.token import estimate_chars_for_tokens, estimate_tokens

__all__ = [
    "Chunk",
    "split_document",
    "estimate_tokens",
    "estimate_chars_for_tokens",
    "chunk_document",
    "chunking_decision",
    "budget_for",
    "prompt_token_overhead",
    "compute_input_budget",
    "ContextBudget",
    "ChunkingDecision",
    "PROMPT_OVERHEAD_TOKENS",
    "BOUNDARY_ORDER",
    "BOUNDARY_PARAGRAPH",
    "BOUNDARY_SENTENCE",
    "BOUNDARY_WORD",
    "BOUNDARY_HARD",
    "BOUNDARY_WHOLE",
]
