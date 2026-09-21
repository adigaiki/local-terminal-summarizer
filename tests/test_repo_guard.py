"""Repository safety guard: detect accidental secrets, local paths, leaks.

The guard is a small local script (`scripts/check_repo_safety.py`); these
tests prove it catches representative leaks and that the repository itself is
clean. No network access is involved.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_repo_safety.py"


def _load_guard(monkeypatch, root: Path):
    spec = importlib.util.spec_from_file_location("check_repo_safety", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Register before execution so dataclasses can resolve string annotations.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "REPO_ROOT", root)
    return module


def test_repository_is_clean():
    result = subprocess.run(
        [sys.executable, str(SCRIPT)], cwd=ROOT, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "clean" in result.stdout


def test_detects_fake_credentials(tmp_path, monkeypatch):
    guard = _load_guard(monkeypatch, tmp_path)
    leak = tmp_path / "leak.txt"
    leak.write_text(
        'api_key = "0123456789abcdef0123456789abcdef"\n'
        "AKIAABCDEFGHIJKLMNOP\n"
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345\n",
        encoding="utf-8",
    )
    rules = {finding.rule for finding in guard.scan_file(leak)}
    assert "assigned-secret" in rules
    assert "aws-access-key" in rules
    assert "bearer-token" in rules


def test_detects_private_key(tmp_path, monkeypatch):
    guard = _load_guard(monkeypatch, tmp_path)
    key = tmp_path / "id.pem"
    key.write_text("-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n", encoding="utf-8")
    rules = {finding.rule for finding in guard.scan_file(key)}
    assert "private-key" in rules or "forbidden-file" in rules


def test_detects_absolute_home_path_and_wildcard_bind(tmp_path, monkeypatch):
    guard = _load_guard(monkeypatch, tmp_path)
    note = tmp_path / "note.py"
    note.write_text(
        'PATH = "/home/alice/secrets/config.toml"\nHOST = "0.0.0.0"\n',
        encoding="utf-8",
    )
    rules = {finding.rule for finding in guard.scan_file(note)}
    assert "absolute-home-path" in rules
    assert "bind-all-address" in rules


@pytest.mark.parametrize("name,rule", [(".env", "forbidden-file"), ("run.log", "temp-artifact")])
def test_detects_forbidden_tracked_names(tmp_path, monkeypatch, name, rule):
    guard = _load_guard(monkeypatch, tmp_path)
    path = tmp_path / name
    path.write_text("harmless-looking\n", encoding="utf-8")
    rules = {finding.rule for finding in guard.scan_file(path)}
    assert rule in rules


def test_allowlisted_file_is_exempt(tmp_path, monkeypatch):
    guard = _load_guard(monkeypatch, tmp_path)
    allowed = tmp_path / "check_repo_safety.py"
    allowed.write_text('aws = "AKIAABCDEFGHIJKLMNOP"\n', encoding="utf-8")
    monkeypatch.setattr(
        guard, "_load_allowlist", lambda: [("check_repo_safety.py", "*")]
    )
    assert guard.scan_file(allowed) == []


# --- allowlist: exact fixture vs. other artifacts ---------------------------


def _make_log_fixture(tmp_path, allowlist_text: str):
    allowlist = tmp_path / "leak_allowlist.txt"
    allowlist.write_text(allowlist_text, encoding="utf-8")
    fixture = tmp_path / "evaluation" / "fixtures" / "logs.log"
    fixture.parent.mkdir(parents=True, exist_ok=True)
    fixture.write_text("2026-01-01 INFO harmless synthetic line\n", encoding="utf-8")
    return allowlist, fixture


def test_exact_allowlisted_log_fixture_is_accepted(tmp_path, monkeypatch):
    guard = _load_guard(monkeypatch, tmp_path)
    allowlist, fixture = _make_log_fixture(
        tmp_path,
        "evaluation/fixtures/logs.log | * | public synthetic evaluation fixture\n",
    )
    monkeypatch.setattr(guard, "ALLOWLIST_PATH", allowlist)

    assert guard.scan_file(fixture) == []


def test_other_log_files_are_still_flagged(tmp_path, monkeypatch):
    guard = _load_guard(monkeypatch, tmp_path)
    allowlist, _ = _make_log_fixture(
        tmp_path,
        "evaluation/fixtures/logs.log | * | public synthetic evaluation fixture\n",
    )
    monkeypatch.setattr(guard, "ALLOWLIST_PATH", allowlist)

    other = tmp_path / "scratch.log"
    other.write_text("ordinary log line\n", encoding="utf-8")
    rules = {finding.rule for finding in guard.scan_file(other)}
    assert "temp-artifact" in rules


def test_path_allowlist_does_not_hide_a_content_secret(tmp_path, monkeypatch):
    guard = _load_guard(monkeypatch, tmp_path)
    allowlist, fixture = _make_log_fixture(
        tmp_path,
        "evaluation/fixtures/logs.log | temp-artifact | public synthetic fixture\n",
    )
    monkeypatch.setattr(guard, "ALLOWLIST_PATH", allowlist)
    fixture.write_text('api_key = "0123456789abcdef0123456789abcdef"\n', encoding="utf-8")

    rules = {finding.rule for finding in guard.scan_file(fixture)}
    assert "temp-artifact" not in rules          # the path rule is allowed
    assert "assigned-secret" in rules            # the content rule is not


def test_malformed_allowlist_entry_is_reported_and_does_not_disable_checks(tmp_path, monkeypatch):
    guard = _load_guard(monkeypatch, tmp_path)
    allowlist, fixture = _make_log_fixture(
        tmp_path,
        "evaluation/fixtures/logs.log |\n",  # missing the rule field
    )
    monkeypatch.setattr(guard, "ALLOWLIST_PATH", allowlist)

    entries, problems = guard.load_allowlist_report()
    assert entries == []
    assert problems and "malformed" in problems[0]

    # The malformed entry must not suppress the finding.
    rules = {finding.rule for finding in guard.scan_file(fixture)}
    assert "temp-artifact" in rules


def test_main_fails_loudly_on_a_malformed_allowlist(tmp_path, monkeypatch, capsys):
    guard = _load_guard(monkeypatch, tmp_path)
    allowlist, _ = _make_log_fixture(
        tmp_path,
        "evaluation/fixtures/logs.log |\n",
    )
    monkeypatch.setattr(guard, "ALLOWLIST_PATH", allowlist)

    code = guard.main([])
    out = capsys.readouterr().out
    assert code == 1
    assert "allowlist problems" in out


# --- public endpoint hygiene ------------------------------------------------


def test_no_wildcard_bind_anywhere_in_tracked_sources():
    # This test file itself contains the literal pattern on purpose.
    for path in list((ROOT / "tests").rglob("*.py")) + list((ROOT / "src").rglob("*.py")):
        if path.name == "test_repo_guard.py":
            continue
        assert "0.0.0.0" not in path.read_text(encoding="utf-8"), path


def test_every_test_server_binds_loopback():
    pattern = re.compile(r"ThreadingHTTPServer\(\s*\(\s*\"([^\"]+)\"")
    found = 0
    for path in (ROOT / "tests").rglob("*.py"):
        for match in pattern.finditer(path.read_text(encoding="utf-8")):
            found += 1
            assert match.group(1) == "127.0.0.1", f"{path}: binds {match.group(1)}"
    assert found >= 2  # the suite really does start local servers
