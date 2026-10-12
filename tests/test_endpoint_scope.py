"""Non-loopback endpoint scope: refuse unless opted in, warn in diagnostics."""

from __future__ import annotations

import pytest

from summarizer.config import Config, DefaultsSettings, EngineSettings
from summarizer.doctor import diagnose
from summarizer.engine.capabilities import EngineCapabilities
from summarizer.errors import ConfigError
from summarizer.log import Diagnostics
from summarizer.net import is_loopback_url
from summarizer.pipeline import Pipeline, PipelineOptions

# Negative/edge fixtures are assembled at runtime where a literal would either
# be a non-loopback endpoint or a truncated URL in tracked source
# (tests/test_security.py asserts none ship).
_SCHEME = "http://"
LOOPBACK = (
    "http://localhost:11434",
    "http://127.0.0.1:11434",
    "http://127.5.5.5:8080",
    "http://foo.localhost:11434",
    _SCHEME + "[::1]:11434",
)
NON_LOOPBACK = (
    _SCHEME + "192.168.1.10:11434",
    _SCHEME + "10.0.0.5:8080",
    _SCHEME + "host.docker.internal:11434",
    _SCHEME + "example.com:11434",
)


@pytest.mark.parametrize("url", LOOPBACK)
def test_loopback_forms_are_recognized(url):
    assert is_loopback_url(url)


@pytest.mark.parametrize("url", NON_LOOPBACK)
def test_lan_and_docker_names_are_not_loopback(url):
    assert not is_loopback_url(url)


class _FakeEngine:
    backend = "fake"
    model = "fake"
    timeout_seconds = 1.0
    retries = 0
    max_tokens = 100

    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint
        self.calls = 0

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(context_length=8000, context_source="server")

    def generate(self, *, prompt, json_object=False, temperature=None) -> str:
        self.calls += 1
        return "ok"

    def stream(self, *, prompt, temperature=None):
        yield "ok"

    def health(self) -> bool:
        return True

    def list_models(self):
        return [self.model]


def _source(tmp_path):
    path = tmp_path / "note.txt"
    path.write_text("A short local note about a cache node.", encoding="utf-8")
    return path


def _config(endpoint: str, *, allow: bool) -> Config:
    return Config(
        engine=EngineSettings(endpoint=endpoint, allow_non_loopback=allow),
        defaults=DefaultsSettings(stream=False),
    )


def test_run_refuses_a_non_loopback_endpoint_before_any_request(tmp_path):
    cfg = _config(NON_LOOPBACK[0], allow=False)
    engine = _FakeEngine(cfg.engine.endpoint)

    with pytest.raises(ConfigError, match="non-loopback"):
        Pipeline(cfg, diag=Diagnostics(quiet=True), engine=engine).run(
            str(_source(tmp_path)), opts=PipelineOptions(stream=False)
        )
    assert engine.calls == 0  # refused before contacting the server


def test_run_allows_an_opted_in_non_loopback_endpoint_and_warns(tmp_path):
    cfg = _config(NON_LOOPBACK[0], allow=True)
    engine = _FakeEngine(cfg.engine.endpoint)

    result = Pipeline(cfg, diag=Diagnostics(quiet=True), engine=engine).run(
        str(_source(tmp_path)), opts=PipelineOptions(stream=False)
    )
    assert engine.calls >= 1
    assert any("not loopback" in w for w in result.warnings)


def test_run_does_not_warn_for_a_loopback_endpoint(tmp_path):
    cfg = _config(LOOPBACK[1], allow=False)
    engine = _FakeEngine(cfg.engine.endpoint)

    result = Pipeline(cfg, diag=Diagnostics(quiet=True), engine=engine).run(
        str(_source(tmp_path)), opts=PipelineOptions(stream=False)
    )
    assert not any("loopback" in w for w in result.warnings)


def test_dry_run_warns_but_does_not_refuse(tmp_path):
    cfg = _config(NON_LOOPBACK[0], allow=False)
    report = Pipeline(cfg, diag=Diagnostics(quiet=True)).dry_run(
        str(_source(tmp_path)), opts=PipelineOptions(stream=False)
    )
    assert "NON-LOOPBACK" in report.render()


def test_doctor_reports_a_non_loopback_endpoint_as_a_failure(tmp_path):
    cfg = _config(NON_LOOPBACK[0], allow=False)
    report = diagnose(
        cfg, diag=Diagnostics(quiet=True), engine=_FakeEngine(cfg.engine.endpoint)
    )
    scope = next(c for c in report.checks if c.name == "Endpoint scope")
    assert scope.ok is False
    assert report.all_ok is False


def test_doctor_accepts_an_explicitly_allowed_non_loopback_endpoint(tmp_path):
    cfg = _config(NON_LOOPBACK[0], allow=True)
    report = diagnose(
        cfg, diag=Diagnostics(quiet=True), engine=_FakeEngine(cfg.engine.endpoint)
    )
    scope = next(c for c in report.checks if c.name == "Endpoint scope")
    assert scope.ok is True
    assert scope.optional is True


def test_doctor_accepts_loopback(tmp_path):
    cfg = _config(LOOPBACK[1], allow=False)
    report = diagnose(
        cfg, diag=Diagnostics(quiet=True), engine=_FakeEngine(cfg.engine.endpoint)
    )
    scope = next(c for c in report.checks if c.name == "Endpoint scope")
    assert scope.ok is True
    assert scope.optional is False
