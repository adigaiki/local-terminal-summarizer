"""Prompt/profile identity: content-addressed, stable, and non-sensitive."""

from __future__ import annotations

import re

from summarizer.profiles import load_profile
from summarizer.prompt import PROMPT_SCHEMA_VERSION, profile_identity, prompt_identity

_HEX = re.compile(r"^[0-9a-f]{16}$")


def test_profile_identity_is_stable_and_opaque():
    first = profile_identity(load_profile("plain"))
    second = profile_identity(load_profile("plain"))
    assert first == second
    assert _HEX.match(first)
    # Never leaks prompt text.
    assert "summariz" not in first.lower()


def test_profile_identity_changes_when_profile_text_changes(tmp_path):
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "custom.md").write_text("Summarize: {{input}}\n", encoding="utf-8")
    before = profile_identity(load_profile("custom", user_dir=prompts))
    (prompts / "custom.md").write_text("Summarize briefly: {{input}}\n", encoding="utf-8")
    after = profile_identity(load_profile("custom", user_dir=prompts))
    assert before != after


def test_profile_identity_is_not_just_the_name(tmp_path):
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "plain.md").write_text("A totally different plain prompt: {{input}}\n", encoding="utf-8")
    override = load_profile("plain", user_dir=prompts)
    assert override.name == "plain"
    assert profile_identity(override) != profile_identity(load_profile("plain"))


def test_prompt_identity_reflects_run_configuration():
    profile = load_profile("plain")
    base = prompt_identity(profile, lang=None, output_format="markdown", has_context=False)
    assert base != prompt_identity(profile, lang="fr", output_format="markdown", has_context=False)
    assert base != prompt_identity(profile, lang=None, output_format="json", has_context=False)
    assert base != prompt_identity(profile, lang=None, output_format="markdown", has_context=True)


def test_reduce_profile_has_a_distinct_identity():
    assert profile_identity(load_profile("plain")) != profile_identity(load_profile("__reduce__"))


def test_schema_version_is_exposed():
    assert PROMPT_SCHEMA_VERSION.startswith("summarizer.prompt.")
