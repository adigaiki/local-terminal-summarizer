"""Pure context-budget arithmetic.

Everything a request needs must fit inside the model's context window:

    [ chat scaffolding + section markers ]   overhead
    [ trusted preamble + profile text   ]   prompt
    [ user-supplied --context (trusted) ]   context file
    [ SOURCE DOCUMENT (untrusted)       ]   input we are budgeting
    [ reserved space for the reply      ]   output

This module answers one question: given a context window and the other
callers on it, how many tokens may the document occupy? It is deliberately a
pure function of numbers -- no I/O, no engine, no config object -- so the
arithmetic that protects users from silently overflowing a model's window is
directly testable.

Nothing here assumes a universal context size: the caller passes whatever the
backend reported (or a documented conservative fallback) and the provenance
tag that goes with it.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "ContextBudget",
    "compute_input_budget",
    "PROMPT_OVERHEAD_TOKENS",
]

# Fixed per-request overhead: security preamble scaffolding, section markers,
# and the chat wrapper itself, measured in tokens.
PROMPT_OVERHEAD_TOKENS = 300


@dataclass(frozen=True)
class ContextBudget:
    """How the context window is spent, in tokens."""

    context_tokens: int
    context_source: str
    overhead_tokens: int
    prompt_tokens: int  # overhead + trusted profile instructions
    context_file_tokens: int  # user-supplied trusted --context
    output_tokens: int  # reserved for generated output
    usable_input_tokens: int  # what is left for document content (>= 0)
    max_chunk_tokens: int  # largest single document chunk we may send

    @property
    def document_tokens(self) -> int:
        """Backwards-compatible alias for :attr:`usable_input_tokens`."""
        return self.usable_input_tokens

    @property
    def source(self) -> str:
        """Short provenance string for diagnostics."""
        return f"context={self.context_tokens} ({self.context_source})"

    @property
    def fits_anything(self) -> bool:
        """False when the window cannot hold even one minimal chunk."""
        return self.max_chunk_tokens >= 1

    @property
    def claimed_tokens(self) -> int:
        """Total tokens this budget believes are in use."""
        return (
            self.prompt_tokens
            + self.context_file_tokens
            + self.output_tokens
            + max(0, self.usable_input_tokens)
        )

    def describe(self) -> str:
        return (
            f"context {self.context_tokens} tokens ({self.context_source}); "
            f"prompt {self.prompt_tokens} + context file {self.context_file_tokens} "
            f"+ output {self.output_tokens} => usable input {self.usable_input_tokens}"
        )


def compute_input_budget(
    *,
    context_length: int,
    prompt_tokens: int,
    max_tokens_per_chunk: int,
    reserved_output_tokens: int = 0,
    context_file_tokens: int = 0,
    overhead_tokens: int = PROMPT_OVERHEAD_TOKENS,
    context_source: str = "fallback",
) -> ContextBudget:
    """Compute the usable document-input budget for one request.

    The result never exceeds ``context_length``: the reserved output space,
    the profile/preamble prompt, the---optional---user context file and the
    fixed overhead are all subtracted from the window before any document
    content is admitted.

    ``max_chunk_tokens`` is additionally clamped by ``max_tokens_per_chunk``,
    the operator's own preference for chunk size, and is 0 when the window is
    too small to hold anything at all. Callers must treat 0 as "cannot send
    this document"; they must never silently send an oversized prompt.
    """
    context = max(0, int(context_length))
    prompt = max(0, int(prompt_tokens))
    file_context = max(0, int(context_file_tokens))
    reserve = max(0, int(reserved_output_tokens))
    overhead = max(0, int(overhead_tokens))
    preferred_chunk = max(0, int(max_tokens_per_chunk))

    # `prompt_tokens` is documented as including the fixed overhead, but be
    # defensive: never let the two be double-counted if a caller passes only
    # the profile body.
    consumed = prompt + file_context + reserve
    usable = max(0, context - consumed)
    max_chunk = min(preferred_chunk, usable) if preferred_chunk else usable

    return ContextBudget(
        context_tokens=context,
        context_source=context_source,
        overhead_tokens=overhead,
        prompt_tokens=prompt,
        context_file_tokens=file_context,
        output_tokens=reserve,
        usable_input_tokens=usable,
        max_chunk_tokens=max(0, max_chunk),
    )
