"""The canonical :class:`Document` produced by all readers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
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
        return self.content.count("\n") + (1 if self.content else 0)

    @property
    def char_count(self) -> int:
        return len(self.content)

    @property
    def token_estimate(self) -> int:
        from summarizer.chunking.token import estimate_tokens

        return estimate_tokens(self.content)

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