"""Evaluation harness: cases, runner, reports, and the CLI listing.

Everything here runs without a model server (no Ollama, no network) using an
in-process engine, plus a `--dry-run` CLI path that contacts nothing.
"""

from __future__ import annotations

import json

import pytest

from summarizer.cli import main
from summarizer.config import ChunkingSettings, Config, DefaultsSettings, EngineSettings
from summarizer.engine import EngineCapabilities
from summarizer.errors import ConfigError
from summarizer.evaluation import (
    DISCLAIMER,
    SCHEMA,
    default_eval_root,
    filter_cases,
    load_cases,
    render_text,
    resolve_eval_root,
    run_cases,
)
from summarizer.log import Diagnostics


class FakeEngine:
    backend = "fake-local"
    endpoint = "http://127.0.0.1:9"
    model = "fake"
    timeout_seconds = 1.0
    retries = 0
    max_tokens = 1024
    reasoning_effort = "none"

    def __init__(self, *, text: str = "Halyard quasar-42 reconcile_ledger disk Maya Bordeaux 3.4") -> None:
        self.text = text

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(backend=self.backend, context_length=4096)

    def generate(self, *, prompt: str, json_object: bool = False, temperature=None) -> str:
        if json_object:
            return json.dumps({"summary": self.text})
        return self.text

    def stream(self, *, prompt: str, temperature=None):
        yield self.text

    def health(self) -> bool:
        return True

    def list_models(self) -> list[str]:
        return [self.model]


def _config() -> Config:
    return Config(
        engine=EngineSettings(context_length=4096),
        defaults=DefaultsSettings(stream=False),
        chunking=ChunkingSettings(max_tokens_per_chunk=3000, overlap_tokens=0, reserve_output_tokens=100),
    )


@pytest.fixture()
def paths():
    return resolve_eval_root(None)


def test_eval_root_is_discoverable_from_repo_root():
    root = default_eval_root()
    assert root is not None
    assert (root / "cases").is_dir() and (root / "fixtures").is_dir()


def test_cases_load_and_fixtures_exist(paths):
    cases = load_cases(paths)
    ids = {case.id for case in cases}
    assert {"academic-faithful", "long-mapreduce", "injection-resisted"} <= ids
    for case in cases:
        assert (paths.fixtures / case.fixture).is_file(), case.fixture


def test_core_and_security_cases_run_without_network(paths):
    cases = filter_cases(load_cases(paths), tags=["core", "security"])
    report = run_cases(
        cases, engine=FakeEngine(), config=_config(), diag=Diagnostics(quiet=True), paths=paths
    )
    assert report.passed, [r.to_json() for r in report.results if not r.passed]
    payload = report.to_json()
    assert payload["schema"] == SCHEMA
    assert payload["disclaimer"] == DISCLAIMER
    assert payload["summary"]["failed"] == 0


def test_long_case_exercises_mapreduce(paths):
    cases = filter_cases(load_cases(paths), tags=["long"])
    report = run_cases(
        cases, engine=FakeEngine(), config=_config(), diag=Diagnostics(quiet=True), paths=paths
    )
    assert report.passed
    assert report.results[0].metrics["strategy"] == "mapreduce"
    assert report.results[0].metrics["chunks"] > 1


def test_missing_sentinel_fails_the_case(paths):
    cases = filter_cases(load_cases(paths), ids=["academic-faithful"])
    report = run_cases(
        cases, engine=FakeEngine(text="nothing relevant"), config=_config(),
        diag=Diagnostics(quiet=True), paths=paths,
    )
    assert not report.passed
    check = next(c for c in report.results[0].checks if c.name == "expected_content_present")
    assert not check.passed


def test_injected_marker_is_a_failure_signal(paths):
    cases = filter_cases(load_cases(paths), ids=["injection-resisted"])
    report = run_cases(
        cases, engine=FakeEngine(text="PWNED-TOKEN-7788"), config=_config(),
        diag=Diagnostics(quiet=True), paths=paths,
    )
    assert not report.passed
    check = next(c for c in report.results[0].checks if c.name == "forbidden_content_absent")
    assert not check.passed


def test_repeats_require_every_repeat_to_pass(paths):
    cases = filter_cases(load_cases(paths), ids=["academic-faithful"])
    report = run_cases(
        cases, engine=FakeEngine(), config=_config(),
        diag=Diagnostics(quiet=True), paths=paths, repeats=3,
    )
    assert report.passed
    assert report.results[0].metrics["repeats"] == 3


def test_unknown_case_id_is_an_error(paths):
    with pytest.raises(ConfigError, match="unknown evaluation case"):
        filter_cases(load_cases(paths), ids=["does-not-exist"])


def test_fixture_failure_is_reported_not_raised(paths):
    from dataclasses import replace

    case = replace(load_cases(paths)[0], fixture="missing-file.txt")
    report = run_cases(
        [case], engine=FakeEngine(), config=_config(), diag=Diagnostics(quiet=True), paths=paths
    )
    assert not report.passed
    assert "fixture not found" in (report.results[0].error or "")


def test_render_text_states_the_disclaimer(paths):
    cases = filter_cases(load_cases(paths), ids=["academic-faithful"])
    report = run_cases(
        cases, engine=FakeEngine(), config=_config(), diag=Diagnostics(quiet=True), paths=paths
    )
    text = render_text(report)
    assert "not a quality score" in text
    assert "PASS" in text


def test_cli_dry_run_lists_cases_without_an_engine(capsys):
    code = main(["evaluate", "--dry-run"])
    captured = capsys.readouterr()
    assert code == 0
    assert "no engine contacted" in captured.out
    assert "academic-faithful" in captured.out
    assert captured.err == ""


def test_cli_dry_run_honours_tags(capsys):
    code = main(["evaluate", "--dry-run", "--tags", "security"])
    captured = capsys.readouterr()
    assert code == 0
    assert "injection-resisted" in captured.out
    assert "academic-faithful" not in captured.out
