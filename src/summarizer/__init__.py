"""summarizer — a local-first, Unix-native terminal summarizer.

The normal summarization path is:

    input -> reader -> document -> chunking -> prompt/profile
            -> local engine -> aggregation -> formatter -> stdout

It never requires the internet and never installs or manages models.
"""

from __future__ import annotations

__version__ = "0.6.0"

__all__ = ["__version__"]