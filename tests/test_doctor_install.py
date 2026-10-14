"""`summarize doctor` reports the running entry point, not the one on PATH.

The two can be different installs: a virtualenv run whose `summarize` is not
first on `PATH` used to be reported with the other install's path, which made
the Python version and extras look wrong.
"""

from __future__ import annotations

import sys
from pathlib import Path

from summarizer.doctor import _installation_detail


def _script(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_reports_the_running_entry_point_even_when_another_is_first_on_path(
    tmp_path, monkeypatch
):
    on_path = _script(tmp_path / "on-path" / "summarize")
    running = _script(tmp_path / "venv" / "bin" / "summarize")
    monkeypatch.setenv("PATH", str(on_path.parent))
    monkeypatch.setattr(sys, "argv", [str(running)])

    detail = _installation_detail("3.12.0")

    assert f"executable: {running.resolve()}" in detail


def test_mismatch_warning_names_both_paths(tmp_path, monkeypatch):
    on_path = _script(tmp_path / "on-path" / "summarize")
    running = _script(tmp_path / "venv" / "bin" / "summarize")
    monkeypatch.setenv("PATH", str(on_path.parent))
    monkeypatch.setattr(sys, "argv", [str(running)])

    detail = _installation_detail("3.12.0")

    warnings = [line for line in detail if line.startswith("!")]
    assert len(warnings) == 1
    assert str(on_path.resolve()) in warnings[0]
    assert str(running.resolve()) in warnings[0]


def test_no_warning_when_path_resolves_to_the_running_entry_point(
    tmp_path, monkeypatch
):
    running = _script(tmp_path / "bin" / "summarize")
    monkeypatch.setenv("PATH", str(running.parent))
    monkeypatch.setattr(sys, "argv", [str(running)])

    detail = _installation_detail("3.12.0")

    assert f"executable: {running.resolve()}" in detail
    assert not [line for line in detail if line.startswith("!")]


def test_falls_back_to_the_module_form_under_python_dash_m(tmp_path, monkeypatch):
    # `python -m summarizer` makes argv[0] the __main__.py shim, which is not
    # the entry point a user would run.
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(sys, "argv", [str(tmp_path / "summarizer" / "__main__.py")])

    detail = _installation_detail("3.12.0")

    assert f"executable: {sys.executable} -m summarizer" in detail
