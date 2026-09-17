"""Reader registry: pick the right reader for a source and enforce limits."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from summarizer.document.model import Document
from summarizer.errors import InputError
from summarizer.log import Diagnostics

__all__ = ["Reader", "read_source", "read_stdin", "read_file", "checked_size", "decode_bytes", "effective_limit"]

# Keep this above any realistically needed stack depth.
_READ_CHUNK = 256 * 1024

# Absolute ceilings. A configured limit of 0 (or a caller that passes nothing)
# must never mean "unlimited": an unbounded stdin stream or a runaway file
# must not be slurped into memory. These are deliberately generous but finite.
ABSOLUTE_MAX_BYTES = 512 * 1024 * 1024  # 512 MiB
ABSOLUTE_MAX_LINES = 10_000_000


def effective_limit(value: int | None, ceiling: int) -> int:
    """Resolve a configured limit, never returning "unlimited".

    A missing, non-positive or nonsensical value falls back to the absolute
    ceiling so resource limits always hold.
    """
    try:
        number = int(value) if value is not None else 0
    except (TypeError, ValueError):
        return ceiling
    if number <= 0:
        return ceiling
    return min(number, ceiling)


class Reader(Protocol):
    """A reader turns some source into exactly one :class:`Document`."""

    def read(self, source: str, *, diag: Diagnostics, options: dict | None = None) -> Document: ...


def read_source(
    source: str,
    *,
    diag: Diagnostics,
    options: dict | None = None,
) -> Document:
    """Read from stdin (`-` or None) or a file path.

    `options` may carry reader-specific settings (encoding, ocr, max_bytes,
    max_lines).
    """
    options = options or {}
    if source is None or source == "-":
        return _read_stdin_impl(diag=diag, options=options)
    return read_file(source, diag=diag, options=options)


def _read_stdin_impl(*, diag: Diagnostics, options: dict) -> Document:
    from summarizer.readers.stdin import read_stdin

    return read_stdin(diag=diag, options=options)


def read_file(source: str, *, diag: Diagnostics, options: dict | None = None) -> Document:
    """Read a named file, choosing a specialized reader by extension/MIME."""
    options = options or {}
    path = Path(source)
    if not path.exists():
        raise InputError(
            f"no such file: {source}",
            hint="check the path, or pipe input via stdin",
        )
    if path.is_dir():
        raise InputError(f"{source} is a directory, not a file")

    suffix = path.suffix.lower()

    if path.name.lower().endswith(".pdf") or suffix == ".pdf":
        from summarizer.readers.pdf import read_pdf

        return read_pdf(path, diag=diag, options=options)
    if suffix in _CODE_SUFFIXES or path.name in ("Makefile", "CMakeLists.txt", "Dockerfile"):
        from summarizer.readers.code import read_code_file

        return read_code_file(path, diag=diag, options=options)
    if suffix in {".md", ".markdown", ".mdown", ".mkd"}:
        from summarizer.readers.markdown import read_markdown_file

        return read_markdown_file(path, diag=diag, options=options)
    from summarizer.readers.text import read_text_file

    return read_text_file(path, diag=diag, options=options)


_CODE_SUFFIXES = {
    ".py", ".rs", ".c", ".h", ".cc", ".cpp", ".hpp", ".go", ".java", ".js",
    ".ts", ".tsx", ".jsx", ".rb", ".php", ".sh", ".bash", ".zsh", ".fish",
    ".toml", ".yaml", ".yml", ".json", ".xml", ".html", ".css", ".sql", ".lua",
    ".swift", ".kt", ".kts", ".scala", ".ex", ".exs", ".erl", ".hs", ".ml",
    ".clj", ".cljs", ".vim", ".tf", ".proto", ".graphql", ".dockerfile", ".ini",
    ".cfg", ".conf", ".txt", ".log",
}


def checked_size(path: Path, max_bytes: int, *, what: str = "input") -> None:
    """Error out early when a file's declared size exceeds the limit."""
    limit = effective_limit(max_bytes, ABSOLUTE_MAX_BYTES)
    try:
        stat = path.stat()
    except OSError as exc:
        raise InputError(f"cannot stat {path}: {exc}") from exc
    if stat.st_size > limit:
        raise InputError(
            f"{what} too large: {path} is {stat.st_size} bytes "
            f"(limit {limit} bytes; see `input.max_bytes` in config)"
        )


def decode_bytes(raw: bytes, encoding: str, *, diag: Diagnostics, what: str = "input") -> str:
    """Decode input bytes with a documented fallback chain.

    Tries the requested encoding strictly first. On failure, retries with
    UTF-8 (lossy) and then latin-1 (lossless) so the program never crashes
    on undecodable input.
    """
    for enc, strict in ((encoding, True), ("utf-8", False), ("latin-1", False)):
        try:
            return raw.decode(enc, errors="strict" if strict else "replace")
        except UnicodeDecodeError:
            diag.warn(
                f"{what} is not valid {enc}; falling back to lossy decoding"
            )
            continue
    # Unreachable: latin-1 decodes every byte.
    return raw.decode("latin-1", errors="replace")