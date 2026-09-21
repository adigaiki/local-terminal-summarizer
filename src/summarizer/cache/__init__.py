"""Optional local cache/checkpoint subsystem."""

from summarizer.cache.store import (
    SCHEMA,
    CacheEntry,
    CacheIdentity,
    CacheStatus,
    LocalCache,
    content_hash,
)

__all__ = [
    "SCHEMA",
    "CacheEntry",
    "CacheIdentity",
    "CacheStatus",
    "LocalCache",
    "content_hash",
]
