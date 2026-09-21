"""Model/configuration diagnostics: doctor and `summarize models`."""

from __future__ import annotations

import json

from summarizer.config import ChunkingSettings, Config, DefaultsSettings, EngineSettings
from summarizer.doctor import diagnose, list_local_models, render_doctor, render_models, run_models
from summarizer.engine import EngineCapabilities
from summarizer.log import Diagnostics
from test_pipeline_and_cli import RecordingEngine, config


def test_doctor_reports_installation_and_cache(capsys):
    from summarizer.config import CacheSettings
    from dataclasses import replace

    engine = RecordingEngine()
    cfg = replace(config(), cache=CacheSettings(mode="off", dir="/tmp/does-not-exist-summarizer"))
    report = diagnose(cfg, diag=Diagnostics(quiet=True), engine=engine)
    names = {check.name for check in report.checks}
    assert "Installation" in names
    assert "Cache" in names
    rendered = render_doctor(report)
    assert "Installation" in rendered
    assert "no local state" in rendered


def test_doctor_reports_config_sources_and_capabilities():
    engine = RecordingEngine()
    report = diagnose(config(), diag=Diagnostics(quiet=True), engine=engine)
    rendered = render_doctor(report)

    assert "Configuration" in rendered
    assert "precedence:" in rendered
    assert "Context window known" in rendered
    assert "5000 tokens" in rendered  # RecordingEngine advertises 5000
    assert "Model listing" in rendered
    assert "caps:" in rendered
    assert report.capabilities is not None
    assert report.backend == "fake-local"


def test_doctor_lists_available_models_when_configured_one_is_missing():
    engine = RecordingEngine()
    engine.model = "missing:1b"
    engine.list_models = lambda: ["qwen3:8b", "llama3.1:8b"]
    report = diagnose(config(), diag=Diagnostics(quiet=True), engine=engine)
    check = next(c for c in report.checks if c.name == "Model available")
    assert not check.ok
    assert any("qwen3:8b" in detail for detail in check.detail)


def test_list_local_models_respects_model_listing_capability():
    class NoListing(RecordingEngine):
        def __init__(self):
            super().__init__()
            self._caps = EngineCapabilities(context_length=1000, model_listing=False)

    assert list_local_models(NoListing(), diag=Diagnostics(quiet=True)) == []


def test_render_models_marks_the_configured_model():
    text = render_models(
        "qwen3:8b", ["qwen3:8b", "other:1b"], backend="ollama", endpoint="http://localhost:11434"
    )
    assert "* qwen3:8b" in text
    assert "  other:1b" in text
    assert "configured model" in text


def test_run_models_json_is_stable(capsys):
    engine = RecordingEngine()
    engine.model = "qwen3:8b"
    engine.list_models = lambda: ["qwen3:8b"]
    code = run_models(config(), diag=Diagnostics(quiet=True), engine=engine, as_json=True)
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["schema"] == "summarizer.models.v1"
    assert payload["configured_model"] == "qwen3:8b"
    assert payload["configured_model_available"] is True
    assert payload["models"] == ["qwen3:8b"]
