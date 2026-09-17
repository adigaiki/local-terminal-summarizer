"""Output: plain/markdown/json formatters, streaming, atomic writes."""

from summarizer.output.atomic import atomic_write
from summarizer.output.json import (
    build_json_envelope,
    dumps_json,
    format_markdown,
    format_plain,
    parse_json_strict,
    generate_json,
)
from summarizer.output.stream import stream_to_io, stream_write

__all__ = [
    "format_plain",
    "format_markdown",
    "build_json_envelope",
    "dumps_json",
    "parse_json_strict",
    "generate_json",
    "stream_to_io",
    "stream_write",
    "atomic_write",
]