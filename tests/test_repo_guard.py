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
