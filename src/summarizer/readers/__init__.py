"""Input readers: stdin, text, markdown, code, and optional PDF."""

from summarizer.readers.code import language_for_path, read_code_file
from summarizer.readers.markdown import read_markdown_file, textualize_markdown
from summarizer.readers.text import read_text_file

__all__ = [
    "read_text_file",
    "read_markdown_file",
    "textualize_markdown",
    "read_code_file",
    "language_for_path",
]