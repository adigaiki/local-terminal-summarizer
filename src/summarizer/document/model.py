"""The canonical :class:`Document` produced by all readers."""

from __future__ import annotations

import json
from bisect import bisect_right
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any

__all__ = ["Document"]


@dataclass(frozen=True)
class Document:
    """A source document with provenance, independent of how it was read.

    The engine never sees raw reader output; it only sees this object, so
    stdin, Markdown, code, and PDF inputs are all equivalent downstream.
    """

    content: str
    source: str
    mime_type: str = "text/plain"
    encoding: str | None = None
    size: int = 0  # bytes consumed from the original source, if known
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def line_count(self) -> int:
        return self.content.count("\n") + (1 if self.content and not self.content.endswith("\n") else 0)

    def annotate_chunks(self, chunks):
        """Attach source ranges without passing reader details to the splitter.

        Ranges describe the new body; overlap is separately reported in chars.
        Lines/pages are one-based inclusive; character offsets are half-open.
        """
        from summarizer.chunking.splitter import annotate_document_chunks

        return annotate_document_chunks(self, chunks)

    @property
    def char_count(self) -> int:
        return len(self.content)

    @property
    def token_estimate(self) -> int:
        from summarizer.chunking.token import estimate_tokens

        return estimate_tokens(self.content)

    @cached_property
    def _line_starts(self) -> tuple[int, ...]:
        return (0, *(i + 1 for i, char in enumerate(self.content) if char == "\n"))

    @cached_property
    def _page_starts(self) -> tuple[int, ...]:
        # Readers supply offsets, but malformed optional metadata must not
        # invent page numbers or make ordinary text provenance fail.
        starts = self.metadata.get("page_starts")
        if not isinstance(starts, (list, tuple)) or not starts:
            return ()
        if any(type(value) is not int or not 0 <= value <= len(self.content) for value in starts):
            return ()
        if starts[0] != 0 or any(a >= b for a, b in zip(starts, starts[1:])):
            return ()
        return tuple(starts)

    def provenance_span(self, start_char: int, end_char: int) -> dict[str, Any]:
        """Locate a half-open character span in this canonical document.

        Character offsets are zero-based Python string offsets, not bytes.
        Lines and pages are one-based, inclusive, and cover actual characters
        in the span (a trailing newline does not claim the following line).
        Empty spans have null line/page ranges. Page separators belong to the
        preceding page; page numbers refer to original reader page positions.
        """
        if not 0 <= start_char <= end_char <= len(self.content):
            raise ValueError("provenance span is outside document content")
        nonempty = start_char < end_char
        pages = self._page_starts
        return {
            "source": self.source,
            "start_char": start_char,
            "end_char": end_char,
            "start_line": bisect_right(self._line_starts, start_char) if nonempty else None,
            "end_line": bisect_right(self._line_starts, end_char - 1) if nonempty else None,
            "start_page": bisect_right(pages, start_char) if nonempty and pages else None,
            "end_page": bisect_right(pages, end_char - 1) if nonempty and pages else None,
        }

    def to_json(self) -> dict[str, Any]:
        output: dict[str, Any] = {
            "source": self.source,
            "mime_type": self.mime_type,
            "encoding": self.encoding,
            "size_bytes": self.size,
            "line_count": self.line_count,
            "char_count": self.char_count,
        }
        if self.metadata:
            output["metadata"] = self.metadata
        return output

    def to_json_string(self) -> str:
        return json.dumps(self.to_json(), indent=2)