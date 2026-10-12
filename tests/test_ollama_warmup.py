"""Ollama warm-up: learn the served window by loading the model first."""

from __future__ import annotations

from summarizer.config import EngineSettings
from summarizer.engine.capabilities import (
    CONTEXT_SOURCE_CONFIG,
    CONTEXT_SOURCE_FALLBACK,
    CONTEXT_SOURCE_SERVER,
    CONTEXT_SOURCE_TRAINED,
    DEFAULT_CONTEXT_LENGTH,
    EngineCapabilities,
)
from summarizer.engine.ollama import OllamaEngine
from summarizer.log import Diagnostics


class _HTTP:
    """Fake Ollama: /api/ps is empty until /api/generate warms the model."""

    def __init__(self, *, warmup_ok: bool = True, served: int = 8192) -> None:
        self.ps_calls = 0
        self.generate_calls = 0
        self.warmup_ok = warmup_ok
        self.served = served

    def get_json(self, path, **kwargs):
        assert path == "/api/ps"
        self.ps_calls += 1
        if self.ps_calls == 1 or not self.warmup_ok:
            return {"models": []}  # not loaded (and warm-up failed, if asked)
        return {"models": [{"name": "m", "context_length": self.served}]}

    def post_json(self, path, payload, **kwargs):
        if path == "/api/show":
            return {"model_info": {"a.context_length": 32768}}
        if path == "/api/generate":
            self.generate_calls += 1
            if not self.warmup_ok:
                raise OSError("warm-up failed")
            return {}
        raise AssertionError(path)


def _engine(http, **settings):
    return OllamaEngine(
        EngineSettings(model="m", timeout_seconds=5, **settings),
        http=http,
        diag=Diagnostics(quiet=True),
    )


def test_warmup_refreshes_to_the_served_window():
    http = _HTTP(served=8192)
    engine = _engine(http)
    assert engine.capabilities().context_source == CONTEXT_SOURCE_TRAINED

    engine.prepare()

    caps = engine.capabilities()
    assert caps.context_length == 8192
    assert caps.context_source == CONTEXT_SOURCE_SERVER
    assert http.generate_calls == 1


def test_warmup_failure_falls_back_conservatively_with_a_warning():
    http = _HTTP(warmup_ok=False)
    engine = _engine(http)

    engine.prepare()

    caps = engine.capabilities()
    assert caps.context_length == DEFAULT_CONTEXT_LENGTH  # not 32768
    assert caps.context_source == CONTEXT_SOURCE_FALLBACK


def test_configured_context_length_wins_and_skips_warmup():
    http = _HTTP()
    engine = _engine(http, context_length=4096)

    engine.prepare()

    caps = engine.capabilities()
    assert caps.context_length == 4096
    assert caps.context_source == CONTEXT_SOURCE_CONFIG
    assert http.generate_calls == 0


class _TrainedEngine:
    backend = "ollama"
    endpoint = "http://127.0.0.1:11434"
    model = "m"
    timeout_seconds = 1.0
    retries = 0
    max_tokens = 100

    def capabilities(self):
        return EngineCapabilities(
            context_length=32768, context_source=CONTEXT_SOURCE_TRAINED
        )

    def health(self):
        return True

    def list_models(self):
        return [self.model]

    def generate(self, *, prompt, json_object=False, temperature=None):
        return "ok"

    def stream(self, *, prompt, temperature=None):
        yield "ok"


def test_doctor_flags_a_trained_maximum_as_a_warning():
    from summarizer.config import Config
    from summarizer.doctor import diagnose

    report = diagnose(
        Config(engine=EngineSettings(model="m")),
        diag=Diagnostics(quiet=True),
        engine=_TrainedEngine(),
    )
    check = next(c for c in report.checks if c.name == "Serving context window")
    assert check.ok is False
    assert check.optional is True  # a warning, not a hard failure
