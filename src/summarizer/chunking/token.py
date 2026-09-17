"""Cheap, deterministic token estimation (no tokenizer dependency)."""

from __future__ import annotations

__all__ = ["estimate_tokens", "estimate_chars_for_tokens"]


def estimate_tokens(text: str) -> int:
    """Approximate token count.

    This is a heuristic (tokens have no universal definition). We blend the
    character-based estimate (~4 chars/token, typical of English tokenizers
    in practice) with the word-based estimate, which skews high for code.
    The result is only used for chunk budgeting; it never feeds the model.
    """
    chars = max(1, len(text))
    words = max(len(text.split()), 1)
    best = max(chars / 4.0, words * 1.4)
    return max(1, int(best + 0.5))


def estimate_chars_for_tokens(tokens: int) -> int:
    """Rough chars-per-token conversion used for overlap tails."""
    return max(1, int(tokens * 4.0))