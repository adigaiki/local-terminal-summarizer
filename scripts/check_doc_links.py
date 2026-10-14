#!/usr/bin/env python3
"""README/docs link and anchor guard.

A small, reviewable, standard-library-only check that every relative link in
the project's documentation resolves: the target file exists, and any
``#fragment`` matches a heading anchor or an explicit HTML anchor in that file.
It reads local files only and never fetches a URL, so it runs offline in CI and
on a laptop.

Only documentation is scanned -- the repository root's markdown and ``docs/``.
The markdown under ``evaluation/fixtures/`` deliberately contains hostile,
fake link-like text, and ``src/summarizer/profiles/prompts/`` holds prompt
templates; neither is documentation, and both would produce false findings.

Run ``python scripts/check_doc_links.py``; exit status is 0 (clean) or 1
(findings). Heading slugs follow GitHub's rules closely (lower-case, drop
punctuation, spaces to hyphens) but are an approximation, so a mismatch is a
finding: the fix is to point at the heading that exists.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# `[text](target)` and `![alt](target)`, with an optional quoted title dropped.
LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*$", re.M)
HTML_ANCHOR = re.compile(r"<a\s+(?:name|id)=\"([^\"]+)\"", re.I)
# A URL with a scheme is somebody else's problem (and this checker is offline).
SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:", re.I)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    target: str


def doc_files(repo_root: Path) -> list[Path]:
    """Documentation markdown: the repository root and ``docs/``, recursively."""
    found = {path for path in repo_root.glob("*.md") if path.is_file()}
    docs = repo_root / "docs"
    if docs.is_dir():
        found |= {path for path in docs.rglob("*.md") if path.is_file()}
    return sorted(found)


def _fenced_lines(text: str) -> set[int]:
    """Zero-based line numbers inside fenced code blocks.

    A link or heading inside a fence is an example, not a link or a heading.
    """
    fenced: set[int] = set()
    inside = False
    for index, line in enumerate(text.splitlines()):
        if line.lstrip().startswith("```"):
            inside = not inside
            fenced.add(index)
        elif inside:
            fenced.add(index)
    return fenced


def links_in(path: Path) -> list[tuple[int, str]]:
    """Link targets with their 1-based line numbers, fenced blocks excluded."""
    text = path.read_text(encoding="utf-8")
    fenced = _fenced_lines(text)
    links: list[tuple[int, str]] = []
    for match in LINK.finditer(text):
        number = text.count("\n", 0, match.start()) + 1
        if number - 1 not in fenced:
            links.append((number, match.group(1)))
    return links


def slug(heading: str) -> str:
    """Approximate GitHub's heading anchor: markup is dropped, then slugified."""
    text = re.sub(r"`([^`]*)`", r"\1", heading)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[^\w\s-]", "", text.lower().strip())
    return re.sub(r"\s+", "-", text)


def anchors(path: Path) -> set[str]:
    """Heading slugs plus explicit HTML anchors in one file, fences excluded."""
    text = path.read_text(encoding="utf-8")
    fenced = _fenced_lines(text)
    body = "\n".join(
        "" if index in fenced else line for index, line in enumerate(text.splitlines())
    )
    return {slug(heading) for heading in HEADING.findall(body)} | set(
        HTML_ANCHOR.findall(body)
    )


def scan(repo_root: Path | None = None) -> list[Finding]:
    global REPO_ROOT
    if repo_root is not None:
        REPO_ROOT = repo_root
    findings: list[Finding] = []
    # Anchors are per-file and a file can be the target of many links.
    cache: dict[Path, set[str]] = {}
    for doc in doc_files(REPO_ROOT):
        rel = str(doc.relative_to(REPO_ROOT))
        for number, target in links_in(doc):
            if SCHEME.match(target) or target.startswith("//"):
                continue
            path_part, _, fragment = target.partition("#")
            dest = doc if not path_part else (doc.parent / path_part).resolve()
            if not dest.is_file():
                findings.append(Finding(rel, number, "missing-file", target))
                continue
            if fragment:
                if dest not in cache:
                    cache[dest] = anchors(dest)
                if fragment not in cache[dest]:
                    findings.append(Finding(rel, number, "missing-anchor", target))
    return findings


def main() -> int:
    findings = scan()
    if not findings:
        print(f"documentation link check: clean ({len(doc_files(REPO_ROOT))} files scanned)")
        return 0
    print("documentation link check: findings")
    print("")
    for finding in findings:
        print(f"  {finding.path}:{finding.line}: {finding.rule}: {finding.target}")
    print("")
    print("Point each link at the file or heading that exists.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
