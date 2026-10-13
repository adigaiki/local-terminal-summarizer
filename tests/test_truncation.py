"""Prompt-truncation detection: thresholds, engine behaviour, pipeline policy."""

from __future__ import annotations

import json

import pytest

from summarizer.chunking.strategies import budget_for
from summarizer.chunking.token import estimate_tokens
from summarizer.config import ChunkingSettings, Config, DefaultsSettings, EngineSettings
from summarizer.engine.capabilities import EngineCapabilities
from summarizer.engine.openai import _truncation_reason
from summarizer.errors import ContextOverflow, TruncationDetected
from summarizer.log import Diagnostics
from summarizer.pipeline import Pipeline, PipelineOptions
from summarizer.profiles import load_profile

_CONTEXT = 2000


# --- threshold arithmetic (numbers come from the measured Ollama sweep) ------


def test_estimator_bias_is_not_flagged():
    # A fully processed prompt reported ~0.80-0.85 of our estimate.
    assert _truncation_reason(est=5000, reported=4000, window=32768) is None
    assert _truncation_reason(est=1109, reported=910, window=4096) is None


def test_clear_shortfall_is_flagged():
    # The measured truncation cases reported 0.20-0.31 of the estimate.
    assert _truncation_reason(est=10080, reported=2050, window=4096) is not None
    assert _truncation_reason(est=6716, reported=2050, window=4096) is not None


def test_stop_at_the_window_is_flagged():
    # Denser tokenizer than the estimate: the server stopped at the window.
    assert _truncation_reason(est=6000, reported=3800, window=4096) is not None
    assert _truncation_reason(est=3900, reported=3400, window=4096) is None


# --- engine behaviour -------------------------------------------------------


class _Engine:
    """Returns low prompt tokens, like a truncated Ollama prompt."""

    backend = "fake"
    endpoint = "http://127.0.0.1:9"
    model = "fake"
    timeout_seconds = 1.0
    retries = 0
    max_tokens = 100

    def __init__(self) -> None:
        self.action = "raise"
        self.last_truncation: str | None = None
        self.calls = 0

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(context_length=_CONTEXT, context_source="server")

    def prepare(self) -> None:
        pass

    def set_truncation_action(self, action: str) -> None:
        self.action = action

    def generate(self, *, prompt, json_object=False, temperature=None) -> str:
        self.calls += 1
        if self.action == "raise":
            raise TruncationDetected("backend processed only 40 of ~2000 prompt tokens")
        self.last_truncation = "test"
        return '"ok"' if json_object else "partial summary"

    def stream(self, *, prompt, temperature=None):
        self.last_truncation = "test"
        yield "partial"

    def health(self) -> bool:
        return True

    def list_models(self):
        return [self.model]


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


def _single_shot_doc(config: Config) -> str:
    usable = budget_for(
        EngineCapabilities(context_length=_CONTEXT, context_source="server"),
        config.chunking,
        load_profile("plain"),
        max_output_tokens=100,
    ).usable_input_tokens
    words = max(1, int(0.9 * usable / 1.4))
    text = "word " * words
    assert estimate_tokens(text) <= usable
    return text


def _run(engine, config, source, **opts):
    return Pipeline(config, diag=Diagnostics(quiet=True), engine=engine).run(
        str(source), opts=PipelineOptions(stream=False, **opts)
    )


def test_pipeline_retries_then_accepts_a_truncation(tmp_path):
    config = _config()
    source = tmp_path / "doc.txt"
    source.write_text(_single_shot_doc(config), encoding="utf-8")
    engine = _Engine()

    result = _run(engine, config, source)

    assert result.truncated is True
    assert engine.calls >= 2  # refused once, then accepted with a warning
    assert any("truncat" in w.lower() for w in result.warnings)


def test_truncation_flag_is_machine_readable_in_json(tmp_path):
    config = _config()
    source = tmp_path / "doc.txt"
    source.write_text(_single_shot_doc(config), encoding="utf-8")

    result = _run(_Engine(), config, source, output_format="json")
    payload = json.loads(result.text)

    assert payload.get("truncated") is True


def test_strict_refuses_a_truncation(tmp_path):
    config = _config()
    source = tmp_path / "doc.txt"
    source.write_text(_single_shot_doc(config), encoding="utf-8")
    engine = _Engine()

    with pytest.raises(ContextOverflow):
        _run(engine, config, source, strict=True)
    assert engine.calls == 1  # no retry under --strict


class _TruncateOnce(_Engine):
    """Truncates the first attempt, then succeeds."""

    def generate(self, *, prompt, json_object=False, temperature=None) -> str:
        self.calls += 1
        if self.calls == 1:
            self.last_truncation = "first attempt"
            raise TruncationDetected("first attempt truncated")
        self.last_truncation = None
        return '"ok"' if json_object else "summary"


def test_a_successful_retry_is_not_flagged_truncated(tmp_path):
    config = _config()
    source = tmp_path / "doc.txt"
    source.write_text(_single_shot_doc(config), encoding="utf-8")

    result = _run(_TruncateOnce(), config, source)

    assert result.truncated is False


class _NoContact(_Engine):
    def __init__(self) -> None:
        super().__init__()
        self.prepared = False
        self.caps_calls = 0

    def prepare(self) -> None:
        self.prepared = True

    def capabilities(self) -> EngineCapabilities:
        self.caps_calls += 1
        return super().capabilities()


def test_dry_run_never_prepares_or_probes_the_backend(tmp_path):
    """--dry-run contacts nothing: no warm-up, no capability probe."""
    config = _config()
    source = tmp_path / "doc.txt"
    source.write_text("A short note.", encoding="utf-8")
    engine = _NoContact()

    report = Pipeline(config, diag=Diagnostics(quiet=True), engine=engine).dry_run(
        str(source), opts=PipelineOptions()
    )

    assert report.render()
    assert engine.prepared is False
    assert engine.caps_calls == 0


class _UnderCount(_Engine):
    """Under-counts ~3.6x: any prompt estimated over ~1100 tokens is truncated.

    The estimator is 3.6x low, so the plan must shrink below ~0.28 of its
    budget before the real prompt fits the window.
    """

    def generate(self, *, prompt, json_object=False, temperature=None) -> str:
        self.calls += 1
        if estimate_tokens(prompt) > 1100:
            raise TruncationDetected("under-counted prompt", ratio=0.28)
        return '"ok"' if json_object else "summary"


def test_ratio_based_retry_converges_on_a_large_underestimate(tmp_path):
    config = _config()
    source = tmp_path / "doc.txt"
    source.write_text(_single_shot_doc(config), encoding="utf-8")
    engine = _UnderCount()

    result = _run(engine, config, source)

    assert result.truncated is False
    assert result.aggregation.name == "mapreduce"
    # One retry, sized from the observed 0.28 ratio (da * 0.9), not a fixed 0.75.
    assert result.stats.get("context_scale") == pytest.approx(0.252, abs=0.01)
    assert engine.calls <= 1 + 8
