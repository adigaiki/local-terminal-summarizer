"""Introspection commands: config, profiles, cache, completions.

`config show` must never print secrets or environment values; completions must
be generated from the real parser without a completion framework.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from summarizer.cache.cli import run_cache
from summarizer.config import (
    CacheSettings,
    ChunkingSettings,
    Config,
    DefaultsSettings,
    EngineSettings,
)
from summarizer.introspect import run_completions, run_config, run_profiles
from summarizer.log import Diagnostics


def _config(tmp_path) -> Config:
    return Config(
        engine=EngineSettings(
            endpoint="http://user:supersecret@127.0.0.1:11434",
            model="qwen3:8b",
        ),
        defaults=DefaultsSettings(profile="plain", stream=False),
        chunking=ChunkingSettings(concurrency=2),
        cache=CacheSettings(mode="readwrite", dir=str(tmp_path / "cache")),
    )


def _args(**kwargs):
    return argparse.Namespace(**kwargs)


# --- config -----------------------------------------------------------------


def test_config_show_redacts_credentials_and_sources(capsys, tmp_path):
    code = run_config(_args(config_action="show"), _config(tmp_path), Diagnostics(quiet=True))
    out = capsys.readouterr().out
    assert code == 0
    assert "supersecret" not in out
    assert "127.0.0.1:11434" in out
    assert "[engine]" in out and "[cache]" in out and "[chunking]" in out
    assert "concurrency = 2" in out
    assert "from " in out  # source attribution


def test_config_path_lists_locations(capsys, tmp_path):
    code = run_config(_args(config_action="path"), _config(tmp_path), Diagnostics(quiet=True))
    out = capsys.readouterr().out
    assert code == 0
    assert "user config:" in out and "cache dir:" in out and "sessions root:" in out


def test_config_validate_reports_without_secrets(capsys, tmp_path):
    code = run_config(_args(config_action="validate"), _config(tmp_path), Diagnostics(quiet=True))
    out = capsys.readouterr().out
    assert code == 0
    assert "valid" in out
    assert "supersecret" not in out


# --- profiles ---------------------------------------------------------------


def test_profiles_text_lists_builtins(capsys):
    code = run_profiles(_args(format=None, names=False), _config(Path("/tmp")), Diagnostics(quiet=True))
    out = capsys.readouterr().out
    assert code == 0
    for name in ("plain", "code", "academic", "meeting"):
        assert name in out
    assert "prompt identity" in out.lower()


def test_profiles_names_mode_is_machine_readable(capsys):
    code = run_profiles(_args(format=None, names=True), _config(Path("/tmp")), Diagnostics(quiet=True))
    out = capsys.readouterr().out.split()
    assert code == 0
    assert "plain" in out and "code" in out
    assert "Profiles" not in " ".join(out)


def test_profiles_json_schema(capsys):
    code = run_profiles(_args(format="json", names=False), _config(Path("/tmp")), Diagnostics(quiet=True))
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["schema"] == "summarizer.profiles.v1"
    names = {entry["name"] for entry in payload["profiles"]}
    assert {"plain", "code"} <= names
    for entry in payload["profiles"]:
        assert len(entry["identity"]) == 16


# --- cache ------------------------------------------------------------------


def test_cache_path_status_clear(capsys, tmp_path):
    config = _config(tmp_path)
    assert run_cache(_args(cache_action="path", format=None), config, Diagnostics(quiet=True)) == 0
    assert str(tmp_path / "cache") in capsys.readouterr().out

    assert run_cache(_args(cache_action="status", format="json"), config, Diagnostics(quiet=True)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "summarizer.cache.status.v1"
    assert payload["mode"] == "readwrite"

    assert run_cache(_args(cache_action="clear", format=None), config, Diagnostics(quiet=True)) == 0
    assert "cleared" in capsys.readouterr().out


# --- completions ------------------------------------------------------------


def test_bash_completion_mentions_commands_and_options(capsys):
    assert run_completions(_args(completion_shell="bash"), _config(Path("/tmp")), Diagnostics(quiet=True)) == 0
    out = capsys.readouterr().out
    assert "complete -F _summarize summarize" in out
    assert "doctor" in out and "profiles" in out and "cache" in out
    assert "--format" in out and "--stats" in out
    assert "summarize profiles --names" in out  # dynamic profile completion


def test_zsh_and_fish_completions_generate(capsys):
    base = Path("/tmp")
    assert run_completions(_args(completion_shell="zsh"), _config(base), Diagnostics(quiet=True)) == 0
    zsh = capsys.readouterr().out
    assert "#compdef summarize" in zsh and "compdef _summarize summarize" in zsh

    assert run_completions(_args(completion_shell="fish"), _config(base), Diagnostics(quiet=True)) == 0
    fish = capsys.readouterr().out
    assert "complete -c summarize" in fish
    assert "profiles" in fish


def test_unknown_shell_is_an_error(capsys):
    assert run_completions(_args(completion_shell="powershell"), _config(Path("/tmp")), Diagnostics(quiet=True)) == 1
