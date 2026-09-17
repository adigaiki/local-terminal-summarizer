"""Boundary-aware text splitting with overlap support.

Cuts are always attempted against the softest boundary available, in this
order of preference:

    paragraph -> sentence -> word -> hard (single-character) cut

A paragraph or sentence that still exceeds the limit is split by the next
softer boundary, and only genuinely pathological input (for example one
enormous unbroken token) falls back to an arbitrary cut. The boundary that
produced a chunk is recorded on the chunk itself, so callers and tests can
assert that a cleaner boundary was used whenever one existed.

Overlap carries the tail of the previous chunk forward so information at
chunk seams is not lost.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

__all__ = [
    "Chunk",
    "split_document",
    "BOUNDARY_ORDER",
    "BOUNDARY_PARAGRAPH",
    "BOUNDARY_SENTENCE",
    "BOUNDARY_WORD",
    "BOUNDARY_HARD",
    "BOUNDARY_WHOLE",
]

BOUNDARY_PARAGRAPH = "paragraph"
BOUNDARY_SENTENCE = "sentence"
BOUNDARY_WORD = "word"
BOUNDARY_HARD = "hard"
# Not a cut: the document fit in one request, so nothing was split.
BOUNDARY_WHOLE = "whole"

# Preferred boundary order, softest first. Exposed so tests (and future
# strategies) can assert the preference is actually honoured.
BOUNDARY_ORDER = (
    BOUNDARY_PARAGRAPH,
    BOUNDARY_SENTENCE,
    BOUNDARY_WORD,
    BOUNDARY_HARD,
)

# Include separators so chunks remain exact slices of the source.
_PARAGRAPH_RE = re.compile(r"[\s\S]+?(?:\n[ \t]*\n|\Z)")


def _measure_tokens(text: str) -> int:
    """Token heuristic kept local to the splitter (see chunking.token)."""
    chars = max(1, len(text))
    words = max(len(text.split()), 1)
    return max(1, int(max(chars / 4.0, words * 1.4) + 0.5))


_UNIT_TO_MEASURE = {
    "tokens": _measure_tokens,
    "chars": lambda text: max(1, len(text)),
}


@dataclass(frozen=True)
class Chunk:
    """One bounded slice of a document, with provenance."""

    index: int
    text: str
    token_estimate: int
    char_count: int
    start_char: int
    end_char: int
    overlap_chars: int = 0
    # Which boundary produced this chunk's end: see BOUNDARY_ORDER.
    boundary: str = BOUNDARY_PARAGRAPH
    # Provenance of the document this chunk came from (filename, "stdin", ...).
    source: str | None = None

    def provenance(
        self,
        *,
        total: int | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """A compact, JSON-safe description of where this chunk came from."""
        return {
            "index": self.index,
            "count": total,
            "source": source if source is not None else self.source,
            "boundary": self.boundary,
            "start_char": self.start_char,
            "end_char": self.end_char,
            "chars": self.char_count,
            "est_tokens": self.token_estimate,
            "overlap_chars": self.overlap_chars,
        }


def split_document(
    text: str,
    *,
    max_units: int,
    overlap_units: int,
    unit: str = "tokens",
    source: str | None = None,
    max_chunks: int = 256,
) -> list[Chunk]:
    """Split ``text`` into chunks that fit within ``max_units`` each.

    ``unit`` is "tokens" (default) or "chars". Each returned chunk reports the
    boundary that ended it, so callers can tell a clean paragraph split from a
    forced cut. ``source`` is carried onto every chunk as provenance.
    """
    if unit not in _UNIT_TO_MEASURE:
        raise ValueError(f"unknown splitting unit {unit!r} (expected tokens or chars)")
    if max_units < 1:
        raise ValueError("max_units must be >= 1")
    measure = _UNIT_TO_MEASURE[unit]
    if overlap_units >= max_units:
        overlap_units = max(0, max_units - 1)

    if not text:
        return []

    # 1. Break into fitting pieces using the nicest available boundaries.
    pieces: list[tuple[str, int, str]] = []
    for para in _PARAGRAPH_RE.finditer(text):
        segment = para.group(0)
        start = para.start()
        if measure(segment) > max_units:
            pieces.extend(_split_fitting(segment, start, max_units, measure))
        else:
            pieces.append((segment, start, BOUNDARY_PARAGRAPH))

    # 2. Greedily group pieces into chunks up to max_units. Later chunks
    # reserve room for their overlap tail; otherwise a body that exactly fits
    # the budget would become too large once the overlap is prepended.
    groups: list[list[tuple[str, int, str]]] = []
    current: list[tuple[str, int, str]] = []
    for piece, start, boundary in pieces:
        limit = max_units if not groups else max(1, max_units - overlap_units)
        candidate = "".join(part for part, _, _ in current) + piece
        if current and measure(candidate) > limit:
            groups.append(current)
            current = []
        current.append((piece, start, boundary))
    if current:
        groups.append(current)

    # 3. Assemble into Chunk objects with overlap tails and provenance.
    chunks: list[Chunk] = []
    previous_body = ""
    for index, group in enumerate(groups):
        if index >= max_chunks:
            from summarizer.errors import InputError
            raise InputError(f"document exceeds maximum of {max_chunks} chunks")
        body = "".join(piece for piece, _, _ in group)
        start_char = group[0][1]
        end_char = group[-1][1] + len(group[-1][0])
        overlap = _overlap_tail(previous_body, overlap_units, unit)
        overlap = _fit_overlap(overlap, max_units - measure(body), measure)
        combined = (overlap + body) if overlap else body
        chunks.append(
            Chunk(
                index=index,
                text=combined,
                token_estimate=_measure_tokens(combined),
                char_count=len(combined),
                start_char=start_char,
                end_char=end_char,
                overlap_chars=len(overlap),
                boundary=group[-1][2],
                source=source,
            )
        )
        previous_body = body
    return chunks


def _split_fitting(
    segment: str,
    start: int,
    max_units: int,
    measure,
) -> list[tuple[str, int, str]]:
    """Split one oversized segment using the softest available boundaries.

    Tries sentence cuts, then word cuts, then a hard character cut. Returns
    (text, absolute_start, boundary) tuples that all fit within max_units.
    """
    for cutter, label in ((_sentence_cuts, BOUNDARY_SENTENCE), (_word_cuts, BOUNDARY_WORD)):
        pieces = _cut_at(segment, start, cutter(segment), label)
        if len(pieces) > 1 and all(measure(piece) <= max_units for piece, _, _ in pieces):
            return pieces
    return _hard_split(segment, start, max_units, measure)


def _cut_at(
    segment: str,
    start: int,
    cuts: list[int],
    boundary: str,
) -> list[tuple[str, int, str]]:
    pieces: list[tuple[str, int, str]] = []
    prev = 0
    for cut in cuts:
        if cut <= prev:
            continue
        piece = segment[prev:cut]
        if piece.strip():
            pieces.append((piece, start + prev, boundary))
        prev = cut
    if prev < len(segment) and segment[prev:].strip():
        # The trailing piece ends where the paragraph ended, so its boundary
        # is cleaner than the cutter that happened to be in play.
        pieces.append((segment[prev:], start + prev, BOUNDARY_PARAGRAPH))
    if not pieces:
        pieces = [(segment, start, BOUNDARY_PARAGRAPH)]
    return pieces


def _sentence_cuts(text: str) -> list[int]:
    cuts = [match.end() for match in re.finditer(r"[.!?](?:\s+|$)", text)]
    return cuts[:-1] if cuts else cuts


def _word_cuts(text: str) -> list[int]:
    return [match.end() for match in re.finditer(r"\s+", text)]


def _hard_split(
    segment: str,
    start: int,
    max_units: int,
    measure,
) -> list[tuple[str, int, str]]:
    """Last-resort cut for input with no usable boundary (e.g. one huge word)."""
    pieces: list[tuple[str, int, str]] = []
    i = 0
    while i < len(segment):
        j = i + 1
        while j <= len(segment) and measure(segment[i:j]) <= max_units:
            j += 1
        if j - 1 <= i:
            # One character already exceeds the budget; make progress anyway.
            pieces.append((segment[i : i + 1], start + i, BOUNDARY_HARD))
            i += 1
        else:
            pieces.append((segment[i : j - 1], start + i, BOUNDARY_HARD))
            i = j - 1
    return pieces


def _overlap_tail(previous_text: str, overlap_units: int, unit: str) -> str:
    """Take a boundary-aligned tail of the previous chunk body."""
    if not previous_text or overlap_units <= 0:
        return ""
    if unit == "chars":
        tail = previous_text[-overlap_units:]
        if " " in tail[1:]:
            tail = tail[tail.find(" ") + 1 :]
        return tail
    from summarizer.chunking.token import estimate_chars_for_tokens

    char_budget = estimate_chars_for_tokens(overlap_units)
    tail = previous_text[-char_budget:]
    space = tail.find(" ")
    if space > 0 and len(tail) - space > 1:
        tail = tail[space + 1 :]
    return tail


def _fit_overlap(tail: str, available_units: int, measure) -> str:
    """Keep as much of an overlap tail as can fit beside its new body."""
    if available_units <= 0 or not tail:
        return ""
    if measure(tail) <= available_units:
        return tail
    # Prefer whole words while shrinking toward the end of the prior chunk.
    words = tail.split()
    while words:
        candidate = " ".join(words)
        if measure(candidate) <= available_units:
            return candidate
        words.pop(0)
    # A single very long word may still be too large. Character trimming is
    # the final fallback and the caller's maximum remains authoritative.
    for start in range(len(tail)):
        candidate = tail[start:]
        if measure(candidate) <= available_units:
            return candidate
    return ""
