"""Refine aggregation (placeholder-friendly scaffold).

Refine is not wired in v1: map-reduce is the aggregation strategy. This
module exists so the aggregation package exposes the seam for future
strategies without the pipeline knowing which one runs.
"""

from __future__ import annotations

from summarizer.aggregation.strategies import AggregationStrategy, choose_strategy

__all__ = ["AggregationStrategy", "choose_strategy", "REFINE_PLANNED"]

REFINE_PLANNED = True  # refine/hierarchical strategies are planned, not built