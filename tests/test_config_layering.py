"""Configuration layering, project-config opt-in, and source reporting.

Precedence under test: CLI > environment > project config > user config >
built-in defaults. Project config is opt-in so a cloned repository cannot
silently change the model or endpoint.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from summarizer import config as config_module
from summarizer.cli import _resolve_config, build_parser
from summarizer.config import (
    SOURCE_BUILTIN,
    SOURCE_CLI,
    SOURCE_ENV,
    SOURCE_PROJECT,
    SOURCE_USER,
    Config,
    EngineSettings,
    load_config,
    project_config_requested,
)
from summarizer.errors import ConfigError
from summarizer.log import Diagnostics
from summarizer.pipeline import Pipeline, PipelineOptions


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Remove every SUMMARIZER_* variable so tests control the whole env."""
    for name in list(config_module._ENV_MAP) + [
        "SUMMARIZER_CONFIG", "SUMMARIZER_PROJECT_CONFIG", "XDG_CONFIG_HOME",
    ]:
        monkeypatch.delenv(name, raising=False)


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


# --- precedence -------------------------------------------------------------


def test_precedence_env_beats_project_beats_user_beats_defaults(tmp_path):
    user = _write(tmp_path / "user.toml", "[engine]\nmodel = 'user-m'\ntimeout_seconds = 11\n")
    project = _write(tmp_path / "project.toml", "[engine]\nmodel = 'project-m'\nretries = 2\n")

    base = load_config(user_path=user, project_path=project)
    # The project layer outranks the user layer for the same key.
    assert base.engine.model == "project-m"
    # Each layer still contributes the keys it alone sets.
    assert base.engine.timeout_seconds == 11
    assert base.engine.retries == 2
    assert base.origin("engine.timeout_seconds").startswith(SOURCE_USER)
    assert base.origin("engine.retries").startswith(SOURCE_PROJECT)

    import os

    os.environ["SUMMARIZER_MODEL"] = "env-m"
    try:
        merged = load_config(user_path=user, project_path=project)
    finally:
        del os.environ["SUMMARIZER_MODEL"]
    assert merged.engine.model == "env-m"
    assert merged.origin("engine.model") == SOURCE_ENV


def test_builtin_defaults_are_the_lowest_layer(tmp_path):
    cfg = load_config(user_path=tmp_path / "absent.toml", project_path=tmp_path / "absent2.toml")
    assert cfg.engine.model == EngineSettings().model
    assert cfg.origin("engine.model") == SOURCE_BUILTIN


# --- project config opt-in ---------------------------------------------------


def test_project_config_is_ignored_without_opt_in(tmp_path, monkeypatch):
    user = _write(tmp_path / "user.toml", "[engine]\nmodel = 'user-m'\n")
    monkeypatch.chdir(tmp_path)
    _write(tmp_path / "summarizer.toml", "[engine]\nmodel = 'project-m'\nendpoint = 'http://localhost:1'\n")

    cfg = load_config(user_path=user)

    assert cfg.engine.model == "user-m"          # project file was not read
    assert cfg.project_config_path is None
    assert cfg.origin("engine.model").startswith(SOURCE_USER)


def test_project_config_is_read_when_opted_in(tmp_path, monkeypatch):
    user = _write(tmp_path / "user.toml", "[engine]\nmodel = 'user-m'\n")
    monkeypatch.chdir(tmp_path)
    project = _write(tmp_path / "summarizer.toml", "[engine]\nmodel = 'project-m'\n")
    monkeypatch.setenv("SUMMARIZER_PROJECT_CONFIG", "1")

    cfg = load_config(user_path=user)

    assert cfg.engine.model == "project-m"       # project layer wins by precedence
    assert cfg.project_config_path == project
    assert cfg.origin("engine.model").startswith(SOURCE_PROJECT)


def test_explicit_project_path_bypasses_the_opt_in_gate(tmp_path):
    project = _write(tmp_path / "custom.toml", "[engine]\nmodel = 'explicit-m'\n")
    cfg = load_config(user_path=tmp_path / "absent.toml", project_path=project)
    assert cfg.engine.model == "explicit-m"


def test_project_config_requested_resolution(tmp_path, monkeypatch):
    monkeypatch.delenv("SUMMARIZER_PROJECT_CONFIG", raising=False)
    assert project_config_requested() is None

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SUMMARIZER_PROJECT_CONFIG", "true")
    assert project_config_requested() == tmp_path / "summarizer.toml"

    monkeypatch.setenv("SUMMARIZER_PROJECT_CONFIG", str(tmp_path / "other.toml"))
    assert project_config_requested() == tmp_path / "other.toml"


# --- backend-specific endpoint defaults -------------------------------------


@pytest.mark.parametrize(
    "backend,endpoint",
    [
        ("llama.cpp", "http://localhost:8080"),
        ("llama-cpp", "http://localhost:8080"),
        ("lmstudio", "http://localhost:1234"),
        ("ollama", "http://localhost:11434"),
    ],
)
def test_unset_endpoint_follows_the_backend_default(tmp_path, backend, endpoint):
    project = _write(tmp_path / "p.toml", f"[engine]\nbackend = '{backend}'\n")
    cfg = load_config(user_path=tmp_path / "absent.toml", project_path=project)
    assert cfg.engine.endpoint == endpoint


def test_explicit_endpoint_is_never_overridden_by_the_backend_default(tmp_path):
    project = _write(
        tmp_path / "p.toml",
        "[engine]\nbackend = 'llama.cpp'\nendpoint = 'http://127.0.0.1:9999'\n",
    )
    cfg = load_config(user_path=tmp_path / "absent.toml", project_path=project)
    assert cfg.engine.endpoint == "http://127.0.0.1:9999"


# --- reasoning alias --------------------------------------------------------


def test_reasoning_alias_maps_to_reasoning_effort(tmp_path):
    project = _write(tmp_path / "p.toml", "[engine]\nreasoning = 'low'\n")
    cfg = load_config(user_path=tmp_path / "absent.toml", project_path=project)
    assert cfg.engine.reasoning_effort == "low"


def test_conflicting_reasoning_keys_are_rejected(tmp_path):
    project = _write(
        tmp_path / "p.toml",
        "[engine]\nreasoning = 'low'\nreasoning_effort = 'high'\n",
    )
    with pytest.raises(ConfigError, match="reasoning"):
        load_config(user_path=tmp_path / "absent.toml", project_path=project)


# --- CLI layer + origins ----------------------------------------------------


def test_cli_override_is_recorded_as_command_line(tmp_path, monkeypatch):
    monkeypatch.setenv("SUMMARIZER_CONFIG", str(tmp_path / "absent.toml"))
    args = build_parser().parse_args(["--model", "cli-model", "--backend", "lmstudio"])
    cfg = _resolve_config(args)
    assert cfg.engine.model == "cli-model"
    assert cfg.origin("engine.model") == SOURCE_CLI
    # Selecting a backend without an endpoint lands on its loopback default,
    # and that is reported as a command-line resolution.
    assert cfg.engine.endpoint == "http://localhost:1234"
    assert cfg.origin("engine.endpoint") == SOURCE_CLI


def test_explicit_cli_endpoint_is_kept(tmp_path, monkeypatch):
    monkeypatch.setenv("SUMMARIZER_CONFIG", str(tmp_path / "absent.toml"))
    args = build_parser().parse_args(["--backend", "lmstudio", "--endpoint", "http://127.0.0.1:9"])
    cfg = _resolve_config(args)
    assert cfg.engine.endpoint == "http://127.0.0.1:9"


# --- dry-run reports the resolved source, never secrets ---------------------


def test_dry_run_reports_config_source(tmp_path, monkeypatch):
    monkeypatch.setenv("SUMMARIZER_CONFIG", str(tmp_path / "absent.toml"))
    source = tmp_path / "note.txt"
    source.write_text("A short local note.", encoding="utf-8")
    cfg = Config(engine=EngineSettings(model="m")).with_origins(
        {"engine.model": "user config (/tmp/config.toml)"}
    )
    report = Pipeline(cfg, diag=Diagnostics(quiet=True)).dry_run(str(source), opts=PipelineOptions())
    rendered = report.render()
    assert "Model:" in rendered
    assert "source:       user config (/tmp/config.toml)" in rendered
    assert "No LLM request" in rendered


def test_dry_run_redacts_endpoint_credentials(tmp_path):
    source = tmp_path / "note.txt"
    source.write_text("A short local note.", encoding="utf-8")
    cfg = Config(engine=EngineSettings(endpoint="http://user:hunter2xyz@127.0.0.1:11434"))
    report = Pipeline(cfg, diag=Diagnostics(quiet=True)).dry_run(str(source), opts=PipelineOptions())
    rendered = report.render()
    assert "hunter2xyz" not in rendered
    assert "127.0.0.1:11434" in rendered
