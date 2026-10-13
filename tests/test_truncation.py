"""Prompt-truncation detection: thresholds, engine behaviour, pipeline policy."""

from __future__ import annotations

import io
import json

import pytest

from summarizer.chunking.strategies import budget_for
from summarizer.chunking.token import estimate_tokens
from summarizer.config import ChunkingSettings, Config, DefaultsSettings, EngineSettings
from summarizer.engine.backends import BackendSpec
from summarizer.engine.capabilities import CONTEXT_SOURCE_SERVER, EngineCapabilities
from summarizer.engine.openai import OpenAICompatEngine, _truncation_tier
from summarizer.errors import ContextOverflow, TruncationDetected
from summarizer.log import Diagnostics
from summarizer.pipeline import Pipeline, PipelineOptions
from summarizer.profiles import load_profile

_CONTEXT = 2000


# --- tier arithmetic (numbers from the measured Ollama 0.35.0 sweep) --------


def _tier(reported, est, window=4096, source=CONTEXT_SOURCE_SERVER):
    return _truncation_tier(
        est=est, reported=reported, window=window, window_source=source
    )


def test_exact_signature_with_a_high_estimate_is_hard():
    # window/2 + 2 == 2050; est 3000 > 1.25 * 2050.
    assert _tier(2050, 3000) == "hard"


def test_exact_signature_with_a_low_estimate_is_soft():
    # est 2000 <= 1.25 * 2050: a match, but not distinguishable from a real count.
    assert _tier(2050, 2000) == "soft"


def test_the_2059_table_case_is_not_flagged():
    # A genuine, fully processed table reported 2059: outside +/-2 of 2050.
    assert _tier(2059, 848) is None
    assert _tier(2059, 3000) is None


def test_just_outside_the_margin_is_not_flagged():
    assert _tier(2047, 3000) is None
    assert _tier(2053, 3000) is None
    assert _tier(2048, 3000) == "hard"  # inside +/-2
    assert _tier(2052, 3000) == "hard"


def test_fingerprint_needs_a_trustworthy_window():
    assert _tier(2050, 3000, source="fallback") is None
    assert _tier(2050, 3000, source="server-trained-max") is None
    assert _tier(2050, 3000, window=None) is None


# --- engine behaviour for each tier -----------------------------------------


def _engine(reported: int, *, source: str = CONTEXT_SOURCE_SERVER) -> OpenAICompatEngine:
    spec = BackendSpec(name="ollama", kind="ollama", usage_reporting=True)
    engine = OpenAICompatEngine(EngineSettings(model="m"), spec=spec)
    engine._capabilities = EngineCapabilities(context_length=4096, context_source=source)
    engine.last_usage = {"prompt_tokens": reported}
    engine._diag = Diagnostics(stream=io.StringIO())
    return engine


def _prompt_est(target: int) -> str:
    # estimate_tokens("word " * n) rounds to ~1.4 * n
    return "word " * max(1, int(target / 1.4))


def test_engine_raises_on_the_hard_signature():
    engine = _engine(2050)
    with pytest.raises(TruncationDetected):
        engine._check_truncation(_prompt_est(3000), streaming=False)
    assert engine.last_truncation == "truncation-signature"


def test_engine_notes_the_soft_signature_once_without_raising():
    engine = _engine(2050)
    prompt = _prompt_est(2000)
    engine._check_truncation(prompt, streaming=False)  # no raise
    engine._check_truncation(prompt, streaming=False)  # second call: still one note
    assert engine.last_truncation is None
    assert engine._diag.stream.getvalue().count("truncation signature") == 1


def test_engine_ignores_the_2059_table_case():
    engine = _engine(2059)
    engine._check_truncation(_prompt_est(3000), streaming=False)
    assert engine.last_truncation is None
    assert "truncation signature" not in engine._diag.stream.getvalue()


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


class _HardSignatureEngine(_Engine):
    """Simulates the hard truncation signature: the server reports ~28% of the
    estimate (reported/est = 0.28), i.e. our estimate is ~3.6x the tokens it
    processed. This is the direction hard detection can raise on
    (reported/est < 0.8); a genuine under-estimate (est below reported) cannot
    trigger a retry, only the soft note.
    """

    def generate(self, *, prompt, json_object=False, temperature=None) -> str:
        self.calls += 1
        if estimate_tokens(prompt) > 1100:
            raise TruncationDetected("hard signature", ratio=0.28)
        return '"ok"' if json_object else "summary"


def test_ratio_based_retry_converges_on_a_hard_truncation(tmp_path):
    config = _config()
    source = tmp_path / "doc.txt"
    source.write_text(_single_shot_doc(config), encoding="utf-8")
    engine = _HardSignatureEngine()

    result = _run(engine, config, source)

    assert result.truncated is False
    assert result.aggregation.name == "mapreduce"
    # One retry, sized from the observed 0.28 ratio (x 0.9), not a fixed 0.75.
    assert result.stats.get("context_scale") == pytest.approx(0.252, abs=0.01)
    assert engine.calls <= 1 + 8
