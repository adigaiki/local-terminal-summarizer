"""Retry-after-overflow and lossy-step visibility.

The token estimator is a heuristic, so a backend can refuse a request the plan
thought fit. The pipeline retries with a smaller chunk budget -- bounded, never
under ``--strict``, and only when the new plan actually produces smaller
chunks -- and the retry warning must reach the JSON envelope. The other lossy
step (truncating an oversized interim summary) must be visible there too.
"""

from __future__ import annotations

import json

import pytest

from summarizer.chunking.strategies import budget_for
from summarizer.chunking.token import estimate_tokens
from summarizer.config import (
    ChunkingSettings,
    Config,
    DefaultsSettings,
    EngineSettings,
)
from summarizer.engine.capabilities import EngineCapabilities
from summarizer.errors import ContextOverflow
from summarizer.log import Diagnostics
from summarizer.pipeline import Pipeline, PipelineOptions
from summarizer.profiles import load_profile

_CONTEXT = 2000


def _config(**chunking) -> Config:
    settings = {
        "max_tokens_per_chunk": 100_000,
        "overlap_tokens": 0,
        "reserve_output_tokens": 0,
        "token_safety_margin": 1.0,
    }
    settings.update(chunking)
    return Config(
        engine=EngineSettings(context_length=_CONTEXT, max_tokens=100),
        chunking=ChunkingSettings(**settings),
        defaults=DefaultsSettings(stream=False),
    )


def _usable(config: Config) -> int:
    caps = EngineCapabilities(context_length=_CONTEXT, context_source="server")
    return budget_for(
        caps, config.chunking, load_profile("plain"), max_output_tokens=100
    ).usable_input_tokens


def _single_shot_doc(config: Config) -> str:
    """A document that fits one call now but not after a 0.75 shrink."""
    usable = _usable(config)
    words = max(1, int(0.9 * usable / 1.4))  # estimate_tokens == round(1.4 * words)
    text = "word " * words
    assert estimate_tokens(text) <= usable, "test document must be single-shot"
    assert estimate_tokens(text) > int(usable * 0.75), "must need chunking on retry"
    return text


class _OverflowOnce:
    """Refuses the first request, then answers normally."""

    backend = "fake"
    endpoint = "http://127.0.0.1:9"
    model = "fake"
    timeout_seconds = 1.0
    retries = 0
    max_tokens = 100

    def __init__(self) -> None:
        self.calls = 0

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(context_length=_CONTEXT, context_source="server")

    def generate(self, *, prompt, json_object=False, temperature=None) -> str:
        self.calls += 1
        if self.calls == 1:
            raise ContextOverflow("the request exceeds the model's context window")
        return '"chunk summary"'

    def stream(self, *, prompt, temperature=None):
        yield self.generate(prompt=prompt)

    def health(self) -> bool:
        return True

    def list_models(self):
        return [self.model]


class _AlwaysOverflow:
    backend = "fake"
    endpoint = "http://127.0.0.1:9"
    model = "fake"
    timeout_seconds = 1.0
    retries = 0
    max_tokens = 100

    def __init__(self) -> None:
        self.calls = 0

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(context_length=_CONTEXT, context_source="server")

    def generate(self, *, prompt, json_object=False, temperature=None) -> str:
        self.calls += 1
        raise ContextOverflow("the request exceeds the model's context window")

    def stream(self, *, prompt, temperature=None):
        yield from ()
        raise ContextOverflow("overflow")

    def health(self) -> bool:
        return True

    def list_models(self):
        return [self.model]


class _HugeInterim:
    """Huge map summaries (to force truncation), short reduce summaries."""

    backend = "fake"
    endpoint = "http://127.0.0.1:9"
    model = "fake"
    timeout_seconds = 1.0
    retries = 0
    max_tokens = 100

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(context_length=_CONTEXT, context_source="server")

    def generate(self, *, prompt, json_object=False, temperature=None) -> str:
        if "INTERIM SUMMARIES" in prompt:
            return '{"summary": "combined"}' if json_object else "combined summary"
        return "blah " * 4000  # ~5,600 estimated tokens

    def stream(self, *, prompt, temperature=None):
        yield self.generate(prompt=prompt)

    def health(self) -> bool:
        return True

    def list_models(self):
        return [self.model]


def _run(engine, config: Config, source, **opts):
    return Pipeline(config, diag=Diagnostics(quiet=True), engine=engine).run(
        str(source), opts=PipelineOptions(stream=False, **opts)
    )


def test_overflow_retries_with_smaller_chunks(tmp_path):
    config = _config()
    source = tmp_path / "doc.txt"
    source.write_text(_single_shot_doc(config), encoding="utf-8")
    engine = _OverflowOnce()

    result = _run(engine, config, source)

    assert result.aggregation.name == "mapreduce"
    assert any("context overflow" in w for w in result.warnings)
    assert result.stats.get("context_scale") == pytest.approx(0.75)


def test_retry_warning_reaches_the_json_envelope(tmp_path):
    config = _config()
    source = tmp_path / "doc.txt"
    source.write_text(_single_shot_doc(config), encoding="utf-8")

    result = _run(_OverflowOnce(), config, source, output_format="json")
    payload = json.loads(result.text)

    assert any("context overflow" in w for w in payload["warnings"])


def test_overflow_is_not_retried_under_strict(tmp_path):
    config = _config()
    source = tmp_path / "doc.txt"
    source.write_text(_single_shot_doc(config), encoding="utf-8")
    engine = _OverflowOnce()

    with pytest.raises(ContextOverflow):
        _run(engine, config, source, strict=True)
    assert engine.calls == 1


def test_overflow_on_a_tiny_document_is_not_retried(tmp_path):
    config = _config()
    source = tmp_path / "tiny.txt"
    source.write_text("one short sentence.", encoding="utf-8")
    engine = _AlwaysOverflow()

    with pytest.raises(ContextOverflow):
        _run(engine, config, source)
    # No smaller chunk is possible, so the retry is skipped entirely.
    assert engine.calls == 1


def test_truncation_warning_reaches_the_json_envelope(tmp_path):
    config = _config()
    source = tmp_path / "big.txt"
    source.write_text("word " * 4000, encoding="utf-8")  # multi-chunk

    result = _run(_HugeInterim(), config, source, output_format="json")
    payload = json.loads(result.text)

    assert any("truncating" in w for w in payload["warnings"])
