"""Introspection commands: `config`, `profiles`, and `completions`.

Kept separate from `cli.py` so argument classification stays readable, and
separate from the pipeline because none of this touches a document or a
model. `config show` is redacting by design: it never prints environment
values or credential material and shows endpoint userinfo as ``***``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from summarizer.config import (
    SOURCE_BUILTIN,
    Config,
    cache_dir,
    data_dir,
    project_config_requested,
    sessions_dir,
    user_config_path,
    user_prompt_dir,
)
from summarizer.errors import SummarizerError
from summarizer.log import Diagnostics, redact_url
from summarizer.profiles import PROMPT_DIR, list_profiles, load_profile
from summarizer.prompt import profile_identity

__all__ = ["run_config", "run_profiles", "run_completions"]


def _home_contracted(path: Path | str) -> str:
    text = str(path)
    try:
        home = str(Path.home())
    except Exception:  # pragma: no cover
        return text
    return text.replace(home, "~") if home and home in text else text


# --- config -----------------------------------------------------------------


def _engine_summary(config: Config, *, engine=None) -> list[tuple[str, str]]:
    endpoint = redact_url(config.engine.endpoint)
    rows = [
        ("backend", config.engine.backend),
        ("model", config.engine.model),
        ("endpoint", endpoint),
        ("timeout_seconds", str(config.engine.timeout_seconds)),
        ("retries", str(config.engine.retries)),
        ("context_length", str(config.engine.context_length)),
        ("temperature", str(config.engine.temperature)),
        ("max_tokens", str(config.engine.max_tokens)),
        ("reasoning_effort", config.engine.reasoning_effort),
        ("max_response_bytes", str(config.engine.max_response_bytes)),
    ]
    return rows


def _render_config_show(config: Config) -> str:
    lines = ["Resolved configuration (redacted; no secrets are printed)", ""]
    lines.append("Sources (highest precedence first):")
    lines.append("  command line > environment > project config > user config > built-in defaults")
    user = user_config_path()
    lines.append(f"  user config:    {_home_contracted(config.user_config_path or user)}"
                 + ("" if config.user_config_path else " (not present)"))
    requested = project_config_requested()
    if config.project_config_path:
        lines.append(f"  project config: {_home_contracted(config.project_config_path)}")
    elif requested:
        lines.append(f"  project config: {_home_contracted(requested)} (opted in, not present)")
    else:
        lines.append("  project config: disabled (SUMMARIZER_PROJECT_CONFIG to opt in)")
    lines.append("")

    lines.append("[engine]")
    for key, value in _engine_summary(config):
        origin = _home_contracted(config.origin("engine." + key))
        lines.append(f"  {key} = {value}    # from {origin}")
    lines += [
        "",
        "[defaults]",
        f"  profile = {config.defaults.profile}",
        f"  output_format = {config.defaults.output_format}",
        f"  stream = {str(config.defaults.stream).lower()}",
        f"  progress = {str(config.defaults.progress).lower()}",
        f"  chunk_strategy = {config.defaults.chunk_strategy}",
        "",
        "[chunking]",
        f"  max_tokens_per_chunk = {config.chunking.max_tokens_per_chunk}",
        f"  overlap_tokens = {config.chunking.overlap_tokens}",
        f"  reserve_output_tokens = {config.chunking.reserve_output_tokens}",
        f"  max_chunks = {config.chunking.max_chunks}",
        f"  concurrency = {config.chunking.concurrency}    # from {_home_contracted(config.origin('chunking.concurrency'))}",
        "",
        "[input]",
        f"  max_bytes = {config.input.max_bytes}",
        f"  max_lines = {config.input.max_lines}",
        f"  encoding = {config.input.encoding}",
        f"  max_pdf_pages = {config.input.max_pdf_pages}",
        f"  max_extracted_bytes = {config.input.max_extracted_bytes}",
        "",
        "[session]",
        f"  enabled = {str(config.session.enabled).lower()}",
        f"  notes_dir = {_home_contracted(config.session.notes_dir)}",
        f"  digest_format = {config.session.digest_format}",
        "",
        "[cache]",
        f"  mode = {config.cache.mode}    # from {_home_contracted(config.origin('cache.mode'))}",
        f"  dir = {_home_contracted(config.cache.dir)}",
        f"  max_entries = {config.cache.max_entries}",
        f"  max_bytes = {config.cache.max_bytes}",
        f"  max_chunk_bytes = {config.cache.max_chunk_bytes}",
        "",
        "[update]",
        f"  mode = {config.update.mode}",
        f"  channel = {config.update.channel}",
        f"  check_on_start = {str(config.update.check_on_start).lower()}",
        f"  url = {redact_url(config.update.url) if config.update.url else '(none)'}",
    ]
    return "\n".join(lines)


def run_config(args: Any, config: Config, diag: Diagnostics) -> int:
    action = getattr(args, "config_action", None)
    if action == "show":
        print(_render_config_show(config))
        return 0
    if action == "path":
        lines = [
            f"user config:    {user_config_path()}",
            f"prompt dir:     {user_prompt_dir()}",
            f"builtin prompts:{PROMPT_DIR}",
            f"sessions root:  {sessions_dir()}",
            f"cache dir:      {cache_dir()}",
        ]
        requested = project_config_requested()
        if requested:
            lines.append(f"project config: {requested}")
        else:
            lines.append("project config: disabled (SUMMARIZER_PROJECT_CONFIG to opt in)")
        print("\n".join(lines))
        return 0
    if action == "validate":
        # Reaching this handler means the loader accepted the configuration.
        # Report what was resolved, without values that could be sensitive.
        print("configuration is valid")
        print(f"  backend: {config.engine.backend}")
        print(f"  model:   {config.engine.model}")
        print(f"  endpoint:{redact_url(config.engine.endpoint)}")
        print(f"  profile: {config.defaults.profile}")
        return 0
    diag.error("config requires one of: show, path, validate")
    return 1


# --- profiles ---------------------------------------------------------------


def _collect_profiles(*, diag: Diagnostics) -> tuple[list[dict[str, Any]], list[str]]:
    user_dir = user_prompt_dir()
    entries: list[dict[str, Any]] = []
    errors: list[str] = []
    for name in list_profiles(user_dir=user_dir):
        try:
            profile = load_profile(name, user_dir=user_dir, diag=diag)
        except SummarizerError as exc:
            errors.append(f"{name}: {exc.message}")
            continue
        entries.append(
            {
                "name": profile.name,
                "source": profile.source,
                "is_reduce": profile.is_reduce,
                "has_input": profile.has_input_slot,
                "identity": profile_identity(profile),
            }
        )
    return entries, errors


def run_profiles(args: Any, config: Config, diag: Diagnostics) -> int:
    entries, errors = _collect_profiles(diag=diag)
    if getattr(args, "names", False):
        for entry in entries:
            print(entry["name"])
        return 0
    if (getattr(args, "format", None) or "").lower() == "json":
        payload = {"schema": "summarizer.profiles.v1", "profiles": entries, "errors": errors}
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    print("Profiles (name, source, prompt identity)")
    print("")
    for entry in entries:
        marker = "" if entry["has_input"] else "  (reduce)"
        print(f"  {entry['name']:<16} {entry['source']:<8} {entry['identity']}{marker}")
    for error in errors:
        print(f"  ! invalid profile: {error}", file=diag.stream)
    user_dir = user_prompt_dir()
    print("")
    print(f"User profiles live in {user_dir}")
    return 0


# --- completions ------------------------------------------------------------

_COMMANDS = "doctor models profiles evaluate config cache session completions"
_FORMATS = "plain markdown json"
_STRATEGIES = "auto tokens chars"
_BACKENDS = "ollama openai-compatible openai llama.cpp lmstudio"
_REASONING = "none low medium high"
_CACHE_MODES = "off read write readwrite"
_SHELLS = "bash zsh fish"
_CONFIG_ACTIONS = "show path validate"
_CACHE_ACTIONS = "status path clear"
_SESSION_ACTIONS = "start end status"

# Options that take a value, mapped to their completion words.
_VALUE_OPTIONS = {
    "--format": _FORMATS,
    "--chunk-strategy": _STRATEGIES,
    "--backend": _BACKENDS,
    "--reasoning-effort": _REASONING,
    "--cache": _CACHE_MODES,
    "--profile": "__profiles__",
}


def _long_options() -> list[str]:
    # Imported lazily to avoid a circular import (cli imports this module).
    from summarizer.cli import build_parser

    options: set[str] = set()
    for action in build_parser()._actions:
        for option in action.option_strings:
            if option.startswith("--"):
                options.add(option)
    return sorted(options)


def _bash_script() -> str:
    opts = " ".join(_long_options())
    lines = [
        "# bash completion for summarize",
        "# install:  source <(summarize completions bash)",
        "#       or:  summarize completions bash > /etc/bash_completion.d/summarize",
        "_summarize() {",
        "    local cur prev",
        '    COMPREPLY=()',
        '    cur="${COMP_WORDS[COMP_CWORD]}"',
        f'    local commands="{_COMMANDS}"',
        f'    local opts="{opts}"',
        '    case "$prev" in',
    ]
    for option, words in _VALUE_OPTIONS.items():
        if words == "__profiles__":
            lines.append(
                f'        {option}) COMPREPLY=( $(compgen -W "$(summarize profiles --names 2>/dev/null)" -- "$cur") ); return;;'
            )
        else:
            lines.append(
                f'        {option}) COMPREPLY=( $(compgen -W "{words}" -- "$cur") ); return;;'
            )
    lines += [
        "    esac",
        '    if [ "$COMP_CWORD" -eq 1 ]; then',
        '        COMPREPLY=( $(compgen -W "$commands" -- "$cur") )',
        "    else",
        '        COMPREPLY=( $(compgen -W "$opts" -- "$cur") )',
        "    fi",
        "}",
        "complete -F _summarize summarize",
        "",
    ]
    return "\n".join(lines)


def _zsh_script() -> str:
    opts = " ".join(_long_options())
    lines = [
        "#compdef summarize",
        "# zsh completion for summarize",
        "# install:  summarize completions zsh > \"${fpath[1]}/_summarize\"",
        "_summarize() {",
        "    local -a commands",
        f"    commands=({_COMMANDS})",
        "    _arguments \\",
        '        "1: :->command" \\',
        '        "*:: :->args" && return 0',
        "    case $state in",
        "        command) _describe 'command' commands ;;",
        "        args) compadd -- " + opts + " ;;",
        "    esac",
        "}",
        "compdef _summarize summarize",
        "",
    ]
    return "\n".join(lines)


def _fish_script() -> str:
    lines = [
        "# fish completion for summarize",
        "# install:  summarize completions fish > ~/.config/fish/completions/summarize.fish",
        "complete -c summarize -f",
    ]
    for command in _COMMANDS.split():
        lines.append(
            f"complete -c summarize -n '__fish_use_subcommand' -a '{command}'"
        )
    for option, words in _VALUE_OPTIONS.items():
        if words == "__profiles__":
            lines.append(
                f"complete -c summarize -l {option.lstrip('-')} -a '(summarize profiles --names 2>/dev/null)'"
            )
        else:
            lines.append(f"complete -c summarize -l {option.lstrip('-')} -a '{words}'")
    for option in _long_options():
        if option in _VALUE_OPTIONS:
            continue
        lines.append(f"complete -c summarize -l {option.lstrip('-')}")
    lines.append("")
    return "\n".join(lines)


def run_completions(args: Any, config: Config, diag: Diagnostics) -> int:
    shell = (getattr(args, "completion_shell", None) or "").lower()
    if shell == "bash":
        print(_bash_script(), end="")
    elif shell == "zsh":
        print(_zsh_script(), end="")
    elif shell == "fish":
        print(_fish_script(), end="")
    else:
        diag.error(f"unknown shell {shell!r}")
        diag.info("hint: supported shells: bash, zsh, fish")
        return 1
    return 0
