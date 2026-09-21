"""Packaging and version handling: one source of truth for the version."""

from __future__ import annotations

import importlib.metadata
import tomllib
from pathlib import Path

import pytest

import summarizer
from summarizer.cli import main

ROOT = Path(__file__).resolve().parents[1]


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_installed_metadata_matches_the_module_version():
    # The editable install must agree with the single source of truth.
    assert importlib.metadata.version("summarizer") == summarizer.__version__


def test_version_flag_prints_the_module_version(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert summarizer.__version__ in capsys.readouterr().out


def test_pyproject_uses_dynamic_version_only():
    project = _pyproject()["project"]
    assert "version" not in project, "version must not be duplicated in pyproject.toml"
    assert project["dynamic"] == ["version"]
    dynamic = _pyproject()["tool"]["setuptools"]["dynamic"]
    assert dynamic["version"] == {"attr": "summarizer.__version__"}


def test_console_script_is_declared():
    assert _pyproject()["project"]["scripts"]["summarize"] == "summarizer.cli:main"


def test_runtime_has_no_dependencies():
    assert _pyproject()["project"]["dependencies"] == []


def test_prompt_package_data_is_declared():
    package_data = _pyproject()["tool"]["setuptools"]["package-data"]["summarizer"]
    assert "profiles/prompts/*.md" in package_data


def test_builtin_prompts_ship_with_the_package():
    from importlib.resources import files

    prompt = files("summarizer") / "profiles" / "prompts" / "plain.md"
    assert prompt.is_file()
