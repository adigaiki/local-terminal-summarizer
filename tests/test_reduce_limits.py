"""Reduce-stage limit regressions: bounded truncation and group invariants.

No model server or external network required: the engine is a stub that
records prompts, and the prompt builder is the real one so trust-boundary
behaviour is exercised end to end.
"""

from __future__ import annotations

import random
import re

import pytest

from summarizer.aggregation.mapreduce import MapReduce, truncate_to_tokens
from summarizer.chunking.token import estimate_tokens
from summarizer.errors import InputError
from summarizer.log import Diagnostics
from summarizer.profiles import load_profile
from summarizer.prompt import PromptBuilder


class StubEngine:
    backend = "stub-local"
    endpoint = "http://127.0.0.1:9"
    model = "stub"
    timeout_seconds = 1.0
    retries = 0

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate(self, *, prompt: str, json_object: bool = False, temperature=None) -> str:
        self.prompts.append(prompt)
        return "round summary text"

    def stream(self, *, prompt: str, temperature=None):
        self.prompts.append(prompt)
        yield "streamed"


@pytest.fixture()
def reduce_profile():
    return load_profile("__reduce__")


@pytest.fixture()
def mapreduce(reduce_profile):
    engine = StubEngine()
    return (
        MapReduce(
            engine,
            PromptBuilder(diag=Diagnostics(quiet=True)),
            diag=Diagnostics(quiet=True),
            reduce_profile=reduce_profile,
        ),
        engine,
    )


@pytest.mark.parametrize("limit", [5, 17, 40, 120])
def test_truncate_to_tokens_always_satisfies_the_estimator(limit):
    text = " ".join(
        "".join("abcdefghij"[(i * 7 + j) % 10] for _ in range((i % 9) + 1))
        for j in range(60)
        for i in range(3)
    )
    assert estimate_tokens(text) > limit
    clipped = truncate_to_tokens(text, limit)
    assert estimate_tokens(clipped) <= limit


def test_truncate_keeps_text_that_already_fits():
    text = "already short"
    assert truncate_to_tokens(text, 10_000) == text
    assert truncate_to_tokens(text, 0) == ""


def test_rounds_wrap_each_group_in_a_fresh_boundary(mapreduce):
    from summarizer.aggregation.mapreduce import REDUCE_SUMMARY_OVERHEAD_TOKENS

    mr, engine = mapreduce
    summaries = [f"interim summary {i} with detail" for i in range(8)]
    # Two stub outputs ("round summary text") plus overhead fit exactly one
    # group, so 8 interim summaries need 4 + 2 = 6 combine calls: >= 2 rounds.
    usable = 2 * (estimate_tokens("round summary text") + REDUCE_SUMMARY_OVERHEAD_TOKENS)
    result = mr.consolidate(
        summaries, lang=None, output_format="plain", usable_tokens=usable
    )
    assert result and all(r == "round summary text" for r in result)
    assert len(engine.prompts) >= 2
    boundaries = []
    for prompt in engine.prompts:
        assert "INTERIM SUMMARIES (UNTRUSTED DATA)" in prompt
        match = re.search(r"<(interim-[0-9a-f]+)-1>", prompt)
        assert match, prompt
        boundaries.append(match.group(1))
    assert len(set(boundaries)) == len(boundaries), "boundaries must be fresh per round"


def test_undersized_reduce_budget_is_a_loud_error(mapreduce):
    mr, engine = mapreduce
    with pytest.raises(InputError, match="budget"):
        mr.consolidate(
            ["one hopelessly oversized interim summary"],
            lang=None,
            output_format="plain",
            usable_tokens=10,
        )
    assert engine.prompts == []  # refused before any generation
