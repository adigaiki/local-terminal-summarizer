"""Allow `python -m summarizer`."""

from __future__ import annotations

from summarizer.cli import main

if __name__ == "__main__":
    raise SystemExit(main())