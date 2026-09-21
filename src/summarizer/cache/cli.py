"""`summarize cache`: inspect, locate, and clear the optional local cache.

Kept simple on purpose: no database, no daemon. The cache is off by default;
these commands let a user see what (if anything) is stored and remove it.
"""

from __future__ import annotations

import json
from typing import Any

from summarizer.cache.store import LocalCache
from summarizer.config import Config
from summarizer.log import Diagnostics

__all__ = ["run_cache"]


def _cache_for(config: Config, diag: Diagnostics) -> LocalCache:
    return LocalCache(config.cache, diag=diag)


def run_cache(args: Any, config: Config, diag: Diagnostics) -> int:
    action = getattr(args, "cache_action", None)
    cache = _cache_for(config, diag)

    if action == "path":
        print(cache.root)
        return 0

    if action == "status":
        status = cache.status()
        if (getattr(args, "format", None) or "").lower() == "json":
            print(json.dumps(status.to_json(), indent=2, sort_keys=True))
        else:
            state = "off" if status.mode == "off" else status.mode
            print(f"cache mode:    {state}")
            print(f"cache path:    {status.path}")
            print(f"entries:       {status.entries} ({status.complete} complete, {status.in_progress} in progress)")
            print(f"size:          {status.bytes} bytes")
            if status.max_entries or status.max_bytes:
                print(f"limits:        {status.max_entries} entries / {status.max_bytes} bytes")
            if status.entries == 0:
                print("")
                print("The cache is empty. Enable it with [cache] mode = \"readwrite\" in config,")
                print("or run `summarize cache path` to see where it would live.")
        return 0

    if action == "clear":
        removed = cache.clear()
        print(f"cleared {removed} cache entr{'y' if removed == 1 else 'ies'} from {cache.root}")
        return 0

    diag.error("cache requires one of: status, path, clear")
    return 1
