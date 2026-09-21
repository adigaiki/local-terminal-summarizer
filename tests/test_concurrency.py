"""Bounded map concurrency: ordering, bounds, failures, cancellation."""

from __future__ import annotations

import re
import threading
import time

import pytest

from summarizer.aggregation.mapreduce import MapReduce
from summarizer.cancel import CancelToken
from summarizer.chunking.splitter import Chunk
from summarizer.engine import EngineCapabilities
from summarizer.errors import EngineError, Interrupted
from summarizer.log import Diagnostics
from summarizer.profiles import load_profile
from summarizer.prompt import PromptBuilder


class ConcurrentEngine:
    backend = "fake-local"
    endpoint = "http://127.0.0.1:9"
    model = "fake"
    timeout_seconds = 1.0
    retries = 0
    max_tokens = 1024

    def __init__(self, *, delay: float = 0.02, fail_at: int | None = None) -> None:
        self.delay = delay
        self.fail_at = fail_at
        self.lock = threading.Lock()
        self.in_flight = 0
        self.max_in_flight = 0
        self.calls: list[int] = []

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(context_length=100000)

    def _index(self, prompt: str) -> int:
        match = re.search(r"CHUNKTOKEN=(\d+)", prompt)
        assert match, "chunk token missing from prompt"
        return int(match.group(1))

    def generate(self, *, prompt: str, json_object: bool = False, temperature=None) -> str:
        index = self._index(prompt)
        with self.lock:
            self.calls.append(index)
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            time.sleep(self.delay)
            if self.fail_at == index:
                raise EngineError(f"boom-{index}")
            return f"summary-{index}"
        finally:
            with self.lock:
                self.in_flight -= 1

    def stream(self, *, prompt: str, temperature=None):
        yield self.generate(prompt=prompt)

    def health(self) -> bool:
        return True

    def list_models(self) -> list[str]:
        return [self.model]


def make_chunks(n: int) -> list[Chunk]:
    chunks = []
    for i in range(n):
        text = f"CHUNKTOKEN={i} " + ("word " * 20)
        chunks.append(
            Chunk(
                index=i, text=text, token_estimate=30, char_count=len(text),
                start_char=i * 100, end_char=i * 100 + len(text), boundary="test",
                source="test",
            )
        )
    return chunks


def make_mr(engine):
    return MapReduce(
        engine,
        PromptBuilder(diag=Diagnostics(quiet=True)),
        diag=Diagnostics(quiet=True),
        reduce_profile=load_profile("__reduce__"),
    )


def run(engine, chunks, *, concurrency=1, cached=None, cancel=None, on_result=None):
    return make_mr(engine).run_map(
        chunks,
        profile=load_profile("plain"),
        context=None,
        lang=None,
        output_format="markdown",
        concurrency=concurrency,
        cancel=cancel,
        cached=cached,
        on_result=on_result,
    )


def test_concurrency_one_preserves_sequential_order_and_count():
    engine = ConcurrentEngine()
    chunks = make_chunks(5)
    summaries = run(engine, chunks, concurrency=1)
    assert summaries == [f"summary-{i}" for i in range(5)]
    assert engine.max_in_flight == 1
    assert engine.calls == [0, 1, 2, 3, 4]


def test_multiple_workers_keep_chunk_order_and_are_bounded():
    engine = ConcurrentEngine(delay=0.03)
    chunks = make_chunks(8)
    summaries = run(engine, chunks, concurrency=4)
    # Results are assembled by chunk index, not completion order.
    assert summaries == [f"summary-{i}" for i in range(8)]
    assert 2 <= engine.max_in_flight <= 4
    assert sorted(engine.calls) == list(range(8))


def test_failure_is_reported_for_the_lowest_failing_chunk():
    engine = ConcurrentEngine(delay=0.02, fail_at=2)
    chunks = make_chunks(6)
    with pytest.raises(EngineError, match="boom-2"):
        run(engine, chunks, concurrency=4)


def test_cached_results_are_reused_and_not_regenerated():
    engine = ConcurrentEngine()
    chunks = make_chunks(5)
    summaries = run(engine, chunks, concurrency=2, cached={1: "cached-1", 3: "cached-3"})
    assert summaries[1] == "cached-1" and summaries[3] == "cached-3"
    assert 1 not in engine.calls and 3 not in engine.calls
    assert summaries[0] == "summary-0" and summaries[4] == "summary-4"


def test_pre_cancelled_token_stops_before_any_generation():
    engine = ConcurrentEngine()
    token = CancelToken(cancelled=True)
    with pytest.raises(Interrupted):
        run(engine, make_chunks(3), concurrency=1, cancel=token)
    assert engine.calls == []


def test_cancellation_between_chunks_stops_remaining_work():
    engine = ConcurrentEngine()
    token = CancelToken()
    done: list[int] = []

    def on_result(index: int, summary: str) -> None:
        done.append(index)
        if index == 0:
            token.cancel()

    with pytest.raises(Interrupted):
        run(engine, make_chunks(4), concurrency=1, cancel=token, on_result=on_result)
    assert done == [0]  # only the first chunk completed


def test_on_result_receives_every_generated_summary():
    engine = ConcurrentEngine()
    seen: dict[int, str] = {}
    summaries = run(engine, make_chunks(4), concurrency=3, on_result=lambda i, s: seen.__setitem__(i, s))
    assert seen == {i: f"summary-{i}" for i in range(4)}
    assert summaries == [f"summary-{i}" for i in range(4)]
