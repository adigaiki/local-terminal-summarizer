"""Markdown reader: light textualization with heading provenance.

The content is passed through mostly intact (code blocks stay intact —
they often matter for code summaries). We strip inline link destinations,
image syntax, emphasis markers, and reference definitions, and we record
headings so the downstream pipeline can show structure.
"""

from __future__ import annotations

import re
from pathlib import Path

from summarizer.document.metadata import file_metadata
from summarizer.document.model import Document
from summarizer.errors import InputError
from summarizer.log import Diagnostics
from summarizer.readers.text import read_text_file

__all__ = ["read_markdown_file", "textualize_markdown"]

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_LINK_RE = re.compile(r"!?\[([^\]]*)\]\(([^)]*)\)", flags=re.MULTILINE)
_REF_DEF_RE = re.compile(r"^\s*\[[^\]]+\]:\s*\S.*$", flags=re.MULTILINE)
_BARE_URL_RE = re.compile(r"<((?:https?|ftp|file)://[^>]+)>")
_EMPH_RE = re.compile(r"(\*\*|__)(.+?)\1|(\*|_)(.+?)\3")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", flags=re.DOTALL)


def textualize_markdown(text: str) -> tuple[str, list[str]]:
    headings: list[str] = []
    kept: list[str] = []

    front_matter: list[str] = []
    in_front_matter = text.startswith("---")
    lines = text.splitlines()

    for line in lines:
        if not kept and in_front_matter:
            if line == "---":
                front_matter.append(line)
                if len(front_matter) == 2:
                    in_front_matter = False
                continue
            front_matter.append(line)
            continue

        if _HTML_COMMENT_RE.search(line):
            line = _HTML_COMMENT_RE.sub("", line)

        if _HEADING_RE.match(line):
            level, title_text = _HEADING_RE.match(line).groups()
            title_text = _clean_inline(title_text)
            headings.append(title_text)
            kept.append(f"{'#' * len(level)} {title_text}")
            continue
        if _REF_DEF_RE.match(line):
            continue
        if _BARE_URL_RE.search(line):
            line = _BARE_URL_RE.sub(r"\1", line)
        line = _clean_inline(line)
        if line.strip():
            kept.append(line)

    content = "\n".join(kept).strip() + "\n"
    if front_matter:
        content = "".join(f"{l}\n" for l in front_matter) + "\n" + content
    return content, headings


def _clean_inline(line: str) -> str:
    line = _LINK_RE.sub(lambda m: m.group(1), line)
    line = _EMPH_RE.sub(lambda m: m.group(2) or m.group(4), line)
    return line


def read_markdown_file(path: Path, *, diag: Diagnostics, options: dict | None = None) -> Document:
    options = options or {}
    plain = read_text_file(path, diag=diag, options=options)
    try:
        content, headings = textualize_markdown(plain.content)
    except Exception as exc:  # textualization must never break summarization
        diag.warn(f"markdown textualization failed for {path}: {exc}; using raw text")
        content, headings = plain.content, []
    meta = file_metadata(path)
    meta["headings"] = headings
    meta["markdown"] = True
    return Document(
        content=content,
        source=str(path),
        mime_type="text/markdown",
        encoding=plain.encoding,
        size=plain.size,
        metadata=meta,
    )