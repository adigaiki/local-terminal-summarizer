"""Backend portability: adapters, the registry, and capability negotiation.

These tests prove the pipeline depends on *capabilities*, not on a backend
name, and that adding an OpenAI-compatible runtime is a catalogue entry.
"""

from __future__ import annotations

from io import StringIO

import pytest

from summarizer.config import Config, DefaultsSettings, EngineSettings
from summarizer.engine import (
    Engine,
    EngineCapabilities,
    OpenAICompatEngine,
    OllamaEngine,
    create_engine,
    canonical_backend_name,
    default_endpoint_for_backend,
    known_backends,
    resolve_backend_spec,
)
from summarizer.engine.capabilities import EngineCapabilities as Caps
from summarizer.errors import ConfigError
from summarizer.log import Diagnostics
from summarizer.pipeline import Pipeline, PipelineOptions
from test_pipeline_and_cli import RecordingEngine, config


class CapturingHttp:
    base_path = ""

    def __init__(self) -> None:
        self.post_calls: list[tuple[str, dict]] = []

    def post_json(self, path, payload):
        self.post_calls.append((path, payload))
        return {"choices": [{"message": {"content": "ok"}}]}

    def stream_sse(self, path, payload):
        yield {"choices": [{"delta": {"content": "ok"}}]}


# --- registry / catalogue ---------------------------------------------------


def test_known_backends_are_canonical_and_loopback():
    names = known_backends()
    assert "ollama" in names and "openai-compatible" in names
    for name in names:
        spec = resolve_backend_spec(name)
        assert spec is not None
        if spec.default_endpoint:
            assert "localhost" in spec.default_endpoint or "127.0.0.1" in spec.default_endpoint


@pytest.mark.parametrize(
    "alias,canonical",
    [
        ("openai", "openai-compatible"),
        ("llama-cpp", "llama.cpp"),
        ("llama_cpp", "llama.cpp"),
        ("lm-studio", "lmstudio"),
        ("lm_studio", "lmstudio"),
        ("OLLAMA", "ollama"),
    ],
)
def test_aliases_resolve_to_canonical_names(alias, canonical):
    assert canonical_backend_name(alias) == canonical


def test_default_endpoints_are_loopback_or_absent():
    assert default_endpoint_for_backend("llama.cpp") == "http://localhost:8080"
    assert default_endpoint_for_backend("lmstudio") == "http://localhost:1234"
    assert default_endpoint_for_backend("nonsense") is None


# --- factory / adapter isolation --------------------------------------------


@pytest.mark.parametrize(
    "backend,expected_cls,expected_label",
    [
        ("ollama", OllamaEngine, "ollama"),
        ("openai", OpenAICompatEngine, "openai-compatible"),
        ("openai-compatible", OpenAICompatEngine, "openai-compatible"),
        ("llama-cpp", OpenAICompatEngine, "llama.cpp"),
        ("lm-studio", OpenAICompatEngine, "lmstudio"),
    ],
)
def test_factory_selects_the_adapter_and_canonical_label(backend, expected_cls, expected_label):
    cfg = Config(engine=EngineSettings(backend=backend), defaults=DefaultsSettings(stream=False))
    engine = create_engine(cfg, diag=Diagnostics(quiet=True))
    assert isinstance(engine, expected_cls)
    assert engine.backend == expected_label
    assert engine.spec is not None and engine.spec.name == expected_label


def test_factory_rejects_unknown_backend_clearly():
    cfg = Config(engine=EngineSettings(backend="not-a-backend"))
    with pytest.raises(ConfigError, match="unknown engine backend"):
        create_engine(cfg, diag=Diagnostics(quiet=True))


def test_openai_compatible_adapters_share_one_implementation():
    """llama.cpp/LM Studio are the generic adapter: no per-backend classes."""
    assert resolve_backend_spec("llama.cpp").kind == "openai-compatible"
    assert resolve_backend_spec("lmstudio").kind == "openai-compatible"


# --- capability negotiation -------------------------------------------------


def test_engine_exposes_capability_accessors():
    cfg = Config(engine=EngineSettings(backend="ollama", context_length=4096))
    engine = create_engine(cfg, diag=Diagnostics(quiet=True))
    assert isinstance(engine, Engine)
    assert engine.supports_streaming() is True
    assert engine.supports_model_listing() is True
    assert engine.supports_reasoning_control() is True
    # Structured output is never assumed without a probe.
    assert engine.supports_structured_output() is None
    caps = engine.capabilities()
    assert caps.context_length == 4096
    assert caps.backend == "ollama"
    assert caps.as_dict()["context_source"] == "config"


def test_capability_snapshot_names_are_stable():
    caps = Caps(streaming=False, structured_json=True, model_listing=False, reasoning_control=None)
    assert caps.supports_streaming is False
    assert caps.supports_structured_output is True
    assert caps.supports_model_listing is False
    assert caps.supports_reasoning_control is None
    snapshot = caps.as_dict()
    assert set(snapshot) >= {
        "backend", "streaming", "structured_json", "model_listing",
        "reasoning_control", "context_length", "context_source",
    }


class NoStreamEngine(RecordingEngine):
    def __init__(self):
        super().__init__()
        self._caps = EngineCapabilities(context_length=5000, streaming=False)
        self.stream_called = False

    def stream(self, *, prompt, temperature=None):
        self.stream_called = True
        yield "should not be used"


def test_pipeline_negotiates_streaming_down_to_generate(tmp_path):
    source = tmp_path / "note.txt"
    source.write_text("Small local note.", encoding="utf-8")
    engine = NoStreamEngine()
    stream = StringIO()
    pipeline = Pipeline(
        config(), diag=Diagnostics(quiet=False, verbose=True, stream=stream), engine=engine
    )

    result = pipeline.run(str(source), opts=PipelineOptions(stream=True))

    assert engine.stream_called is False
    assert result.summary.startswith("summary")
    assert "no streaming support" in stream.getvalue()


def test_pipeline_can_still_stream_when_supported(tmp_path):
    source = tmp_path / "note.txt"
    source.write_text("Small local note.", encoding="utf-8")
    engine = RecordingEngine()  # capabilities default to streaming=True
    pipeline = Pipeline(config(), diag=Diagnostics(quiet=True), engine=engine)

    result = pipeline.run(str(source), opts=PipelineOptions(stream=True))

    assert result.summary == "streamed"


# --- reasoning controls -----------------------------------------------------


def test_reasoning_request_on_generic_backend_is_omitted_and_reported():
    http = CapturingHttp()
    stream = StringIO()
    engine = OpenAICompatEngine(
        EngineSettings(max_tokens=64, reasoning_effort="high"),
        diag=Diagnostics(quiet=False, verbose=True, stream=stream),
        http=http,
    )
    engine.generate(prompt="p")
    payload = http.post_calls[0][1]
    assert "reasoning_effort" not in payload
    assert "does not advertise reasoning control" in stream.getvalue()


def test_reasoning_supported_backend_sends_the_requested_budget():
    http = CapturingHttp()
    engine = OllamaEngine(EngineSettings(max_tokens=64, reasoning_effort="low"), http=http)
    engine.generate(prompt="p")
    assert http.post_calls[0][1]["reasoning_effort"] == "low"
