"""Verbatim Markdown reader: no rendering, execution, or link fetching."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import re

from summarizer.log import Diagnostics
from summarizer.readers.text import read_text_file


def textualize_markdown(text: str) -> tuple[str, list[str]]:
    """Compatibility name: preserve source exactly, collect bounded headings."""
    headings = []
    fence = None
    for line in text.splitlines():
        match = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if match:
            mark, rest = match.groups()
            if fence is None:
                fence = mark
            elif mark[0] == fence[0] and len(mark) >= len(fence) and not rest.strip():
                fence = None
            continue
        if fence is None and len(headings) < 200:
            heading = re.match(r"^ {0,3}#{1,6}\s+(.+)$", line)
            if heading:
                headings.append(heading.group(1)[:200])
    return text, headings


def read_markdown_file(path: Path, *, diag: Diagnostics, options: dict | None = None):
    plain = read_text_file(path, diag=diag, options=options)
    content, headings = textualize_markdown(plain.content)
    return replace(plain, content=content, mime_type="text/markdown",
                   metadata={**plain.metadata, "markdown": True, "headings": headings})
