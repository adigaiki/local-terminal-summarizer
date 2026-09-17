"""Aggregation strategy selection.

For oversized documents we implement map-reduce. The layer is kept abstract
so refine/refine-style or hierarchical strategies can be added without
touching the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass

from summarizer.chunking.strategies import ChunkingDecision

__all__ = ["AggregationStrategy", "choose_strategy"]


@dataclass(frozen=True)
class AggregationStrategy:
    name: str  # "direct" | "mapreduce" | "refine"
    reason: str

    @property
    def is_aggregated(self) -> bool:
        return self.name != "direct"


def choose_strategy(decision: ChunkingDecision) -> AggregationStrategy:
    if decision.single_shot or decision.chunk_count <= 1:
        return AggregationStrategy("direct", "document fits in context; no aggregation needed")
    return AggregationStrategy(
        "mapreduce",
        f"document needs {decision.chunk_count} chunks; summarizing per chunk "
        "then reducing into a final summary",
    )