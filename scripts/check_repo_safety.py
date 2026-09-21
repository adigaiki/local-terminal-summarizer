#!/usr/bin/env python3
"""Repository safety / leak guard.

A small, reviewable, standard-library-only check that makes it hard to
accidentally commit credentials, local paths, local config, temporary
artifacts, or bind-all network addresses into this public repository.

Design goals:

  * No network access, ever. It only reads files already on disk.
  * No giant security framework: one readable list of patterns, one
    allowlist file with a reason per entry.
  * Explainable findings: every hit prints ``path:line: rule`` plus a
    redacted preview, so a false positive is obvious and easy to allow-list.
  * Understandable: run ``python scripts/check_repo_safety.py``; exit status
    is 0 (clean) or 1 (findings).

The checker scans the files git would include (tracked plus untracked,
non-ignored) and falls back to walking the tree when git is unavailable.
Binary files are skipped.

Allowlist entries live in ``scripts/leak_allowlist.txt`` and are of the form::

    path-fragment | rule | why this is safe

``rule`` may be ``*``. An empty or comment (``#``) line is ignored. A
malformed entry allows nothing and is reported (the guard fails loudly rather
than silently ignoring it). The allowlist applies to both path-based findings
(forbidden files, temporary artifacts) and content rules, but allowing a path
rule never suppresses a content rule.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ALLOWLIST_PATH = Path(__file__).resolve().parent / "leak_allowlist.txt"

# Skip obviously binary/vendored trees even when git is unavailable.
SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "build",
    "dist",
}
SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz",
    ".tar", ".whl", ".so", ".pyc", ".woff", ".woff2", ".ttf",
}


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern[str]
    description: str


def _rule(name: str, pattern: str, description: str) -> Rule:
    return Rule(name, re.compile(pattern), description)


# Patterns are intentionally conservative to keep false positives rare and
# explainable. A hit is a prompt to look, not a verdict.
RULES: list[Rule] = [
    _rule(
        "private-key",
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----",
        "embedded private key",
    ),
    _rule(
        "aws-access-key",
        r"\bAKIA[0-9A-Z]{16}\b",
        "AWS access key id",
    ),
    _rule(
        "github-token",
        r"\bgh[pousr]_[A-Za-z0-9]{36,}\b",
        "GitHub token",
    ),
    _rule(
        "slack-token",
        r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b",
        "Slack token",
    ),
    _rule(
        "openai-key",
        r"\bsk-[A-Za-z0-9]{32,}\b",
        "OpenAI-style API key",
    ),
    _rule(
        "google-api-key",
        r"\bAIza[0-9A-Za-z_\-]{35}\b",
        "Google API key",
    ),
    _rule(
        "bearer-token",
        r"(?i)\bbearer\s+[A-Za-z0-9._\-]{20,}",
        "hard-coded bearer token",
    ),
    _rule(
        "assigned-secret",
        r"(?i)\b(api[_-]?key|apikey|secret|password|passwd|access[_-]?token|"
        r"auth[_-]?token|authorization|cookie)\b\s*[:=]\s*[\"'][^\"'\s]{8,}[\"']",
        "credential-like assignment with a literal value",
    ),
    _rule(
        "absolute-home-path",
        r"/(?:home|Users)/[A-Za-z0-9._-]+/",
        "absolute home-directory path",
    ),
    _rule(
        "bind-all-address",
        r"\b0\.0\.0\.0\b",
        "bind-all address (test servers must stay on 127.0.0.1)",
    ),
]

# Files that should never be tracked at all, checked by name.
FORBIDDEN_TRACKED_NAMES = {".env"}
FORBIDDEN_TRACKED_SUFFIXES = (".pem", ".key", ".p12", ".pfx")
# Temporary artifacts that must not be committed.
ARTIFACT_SUFFIXES = (".log", ".tmp", ".bak", ".orig", ".rej", ".prof")


def _is_skipped_path(path: Path) -> bool:
    parts = set(path.parts)
    if parts & SKIP_DIRS:
        return True
    return path.suffix.lower() in SKIP_SUFFIXES


def _parse_allowlist(text: str) -> tuple[list[tuple[str, str]], list[str]]:
    """Parse allowlist text into ``(entries, problems)``.

    A malformed line is dropped (it must never allow anything) but recorded as
    a problem so it is surfaced rather than silently ignored.
    """
    entries: list[tuple[str, str]] = []
    problems: list[str] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = [field.strip() for field in line.split("|")]
        if len(fields) < 2 or not fields[0] or not fields[1]:
            problems.append(
                f"line {lineno}: malformed allowlist entry {raw!r} "
                "(expected 'path-fragment | rule | reason')"
            )
            continue
        entries.append((fields[0], fields[1]))
    return entries, problems


def load_allowlist_report() -> tuple[list[tuple[str, str]], list[str]]:
    """Return ``(valid entries, malformed-line problems)`` for the allowlist."""
    if not ALLOWLIST_PATH.is_file():
        return [], []
    return _parse_allowlist(ALLOWLIST_PATH.read_text(encoding="utf-8"))


def _load_allowlist() -> list[tuple[str, str]]:
    return load_allowlist_report()[0]


def _is_allowed(allowlist: list[tuple[str, str]], rel: str, rule: str) -> bool:
    for path_fragment, allowed_rule in allowlist:
        if path_fragment in rel and allowed_rule in ("*", rule):
            return True
    return False


def tracked_files() -> list[Path]:
    """Files git would include (tracked + untracked, non-ignored)."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return sorted(
            path for path in REPO_ROOT.rglob("*")
            if path.is_file() and not _is_skipped_path(path.relative_to(REPO_ROOT))
        )
    files: list[Path] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        path = REPO_ROOT / line
        if path.is_file():
            files.append(path)
    return files


@dataclass
class Finding:
    path: str
    line: int
    rule: str
    preview: str


def _preview(text: str) -> str:
    """Never echo a whole secret; show a short, obviously-redacted excerpt."""
    text = text.strip()
    if len(text) <= 12:
        return text
    return text[:6] + "…" + text[-4:]


def scan_file(path: Path) -> list[Finding]:
    rel = str(path.relative_to(REPO_ROOT))
    allowlist = _load_allowlist()

    # Name-based findings (forbidden files, temporary artifacts) are filtered
    # through the allowlist too. This is the part that lets an exact entry
    # such as ``evaluation/fixtures/logs.log | * | ...`` suppress *only* that
    # file while ``*.log`` stays flagged everywhere else.
    findings: list[Finding] = []
    lower = path.name.lower()
    if lower in FORBIDDEN_TRACKED_NAMES:
        findings.append(Finding(rel, 0, "forbidden-file", path.name))
    if lower.endswith(FORBIDDEN_TRACKED_SUFFIXES):
        findings.append(Finding(rel, 0, "forbidden-file", path.name))
    if lower.endswith(ARTIFACT_SUFFIXES):
        findings.append(Finding(rel, 0, "temp-artifact", path.name))
    findings = [f for f in findings if not _is_allowed(allowlist, rel, f.rule)]

    # Always continue to content scanning: allowlisting a *path* rule must not
    # hide an actual credential inside the file.
    try:
        raw = path.read_bytes()
    except OSError:
        return findings
    if b"\x00" in raw[:8192]:
        return findings
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return findings

    for lineno, line in enumerate(text.splitlines(), start=1):
        for rule in RULES:
            if rule.pattern.search(line):
                if _is_allowed(allowlist, rel, rule.name):
                    continue
                findings.append(Finding(rel, lineno, rule.name, _preview(line)))
    return findings


def scan(repo_root: Path | None = None) -> list[Finding]:
    global REPO_ROOT
    if repo_root is not None:
        REPO_ROOT = repo_root
    findings: list[Finding] = []
    for path in tracked_files():
        findings.extend(scan_file(path))
    return findings


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    _, problems = load_allowlist_report()
    findings = scan()
    if problems:
        print("repository safety check: allowlist problems")
        print("")
        for problem in problems:
            print(f"  {ALLOWLIST_PATH.relative_to(REPO_ROOT)}: {problem}")
        print("")
    if not findings and not problems:
        print(f"repository safety check: clean ({len(tracked_files())} files scanned)")
        return 0
    if findings:
        print("repository safety check: findings")
        print("")
        for finding in findings:
            location = f"{finding.path}:{finding.line}" if finding.line else finding.path
            print(f"  {location}: {finding.rule}: {finding.preview}")
        print("")
        print("Resolve each finding, or add an entry with a reason to")
        print(f"  {ALLOWLIST_PATH.relative_to(REPO_ROOT)}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
