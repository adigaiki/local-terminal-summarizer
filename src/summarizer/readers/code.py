"""Code reader: passes source through intact, records the language.

We deliberately do not strip comments or attempt to "understand" the code:
transformations could remove the very content a code summary needs. The
reader only adds provenance.
"""

from __future__ import annotations

from pathlib import Path

from summarizer.document.metadata import file_metadata
from summarizer.document.model import Document
from summarizer.log import Diagnostics
from summarizer.readers.text import read_text_file

__all__ = ["read_code_file", "language_for_path"]

_LANGUAGE_BY_SUFFIX = {
    ".py": "python", ".rs": "rust", ".c": "c", ".h": "c", ".cc": "c++",
    ".cpp": "c++", ".hpp": "c++", ".go": "go", ".java": "java", ".js": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".jsx": "javascript", ".rb": "ruby",
    ".php": "php", ".sh": "shell", ".bash": "shell", ".zsh": "shell",
    ".toml": "toml", ".yaml": "yaml", ".yml": "yaml", ".json": "json",
    ".xml": "xml", ".html": "html", ".css": "css", ".sql": "sql", ".lua": "lua",
    ".swift": "swift", ".kt": "kotlin", ".kts": "kotlin", ".scala": "scala",
    ".ex": "elixir", ".exs": "elixir", ".erl": "erlang", ".hs": "haskell",
    ".ml": "ocaml", ".clj": "clojure", ".vim": "vim", ".tf": "terraform",
    ".proto": "protobuf", ".graphql": "graphql", ".ini": "ini", ".cfg": "ini",
    ".conf": "conf", ".txt": "text", ".log": "log",
}

_SPECIAL_FILENAMES = {
    "Makefile": "make", "CMakeLists.txt": "cmake", "Dockerfile": "dockerfile",
    "dockerfile": "dockerfile", ".bashrc": "shell", ".zshrc": "shell",
}


def language_for_path(path: Path) -> str:
    if path.name in _SPECIAL_FILENAMES:
        return _SPECIAL_FILENAMES[path.name]
    return _LANGUAGE_BY_SUFFIX.get(path.suffix.lower(), "text")


def read_code_file(path: Path, *, diag: Diagnostics, options: dict | None = None) -> Document:
    options = options or {}
    plain = read_text_file(path, diag=diag, options=options)
    meta = file_metadata(path)
    meta["language"] = language_for_path(path)
    meta["code"] = True
    return Document(
        content=plain.content,
        source=str(path),
        mime_type="text/x-code",
        encoding=plain.encoding,
        size=plain.size,
        metadata=meta,
    )