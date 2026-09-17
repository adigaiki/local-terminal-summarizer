"""Sessions: opt-in notes with locking and compact pointers."""

from summarizer.session.digest import append_note, make_note, one_sentence_extract
from summarizer.session.lock import FileLock
from summarizer.session.manager import SessionManager, SessionStatus

__all__ = [
    "SessionManager",
    "SessionStatus",
    "FileLock",
    "append_note",
    "make_note",
    "one_sentence_extract",
]