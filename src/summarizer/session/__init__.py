"""Sessions: named, explicit, file-backed run digests.

The subsystem is independent of readers, engines, chunking and model logic:
it consumes a small run event and writes compact pointers.
"""

from summarizer.session.digest import (
    DIGEST_JSON,
    DIGEST_MARKDOWN,
    one_sentence_extract,
    render_digest_json,
    render_digest_markdown,
    write_digest,
)
from summarizer.session.lock import FileLock
from summarizer.session.manager import (
    SESSION_SCHEMA,
    SessionInfo,
    SessionManager,
    human_age,
    render_status,
    validate_name,
)
from summarizer.session.run import ENV_VAR, SessionRun, record_selected, select_session

__all__ = [
    "SessionManager",
    "SessionInfo",
    "SessionRun",
    "SESSION_SCHEMA",
    "ENV_VAR",
    "FileLock",
    "select_session",
    "record_selected",
    "validate_name",
    "human_age",
    "render_status",
    "one_sentence_extract",
    "render_digest_markdown",
    "render_digest_json",
    "write_digest",
    "DIGEST_MARKDOWN",
    "DIGEST_JSON",
]
