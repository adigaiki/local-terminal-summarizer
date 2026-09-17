"""Document model, reader registry, and shared input plumbing."""

from summarizer.document.metadata import file_metadata, merge_metadata
from summarizer.document.model import Document
from summarizer.document.reader import Reader, checked_size, decode_bytes, read_file, read_source

__all__ = [
    "Document",
    "Reader",
    "read_source",
    "read_file",
    "checked_size",
    "decode_bytes",
    "file_metadata",
    "merge_metadata",
]