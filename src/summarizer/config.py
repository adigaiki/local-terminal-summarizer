"""Configuration loading with documented precedence.

Precedence (highest wins):

    CLI  ->  environment  ->  project config  ->  user config  ->  built-in defaults

CLI overrides are applied by cli.py on top of the resolved :class:`Config`.
Project config is `./summarizer.toml` relative to the current working
directory (deliberately simple for v1; the loader is layered so additional
discovery can be added without restructuring). User config lives at
`~/.config/summarizer/config.toml` (or `$XDG_CONFIG_HOME/summarizer/config.toml`).
`$SUMMARIZER_CONFIG` overrides the user config path.

Malformed TOML and type errors raise :class:`ConfigError` with the file path
and, where available, line/key information. A user's config is never
overwritten or mutated.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from summarizer.errors import ConfigError

__all__ = [
    "Config",
    "EngineSettings",
    "DefaultsSettings",
    "ChunkingSettings",
    "InputSettings",
    "SessionSettings",
    "UpdateSettings",
    "load_config",
    "user_config_path",
    "user_prompt_dir",
    "project_config_path",
    "data_dir",
    "sessions_dir",
]

ENV_PREFIX = "SUMMARIZER_"

BUILTIN_DEFAULTS: dict[str, Any] = {
    "engine": {
        "backend": "ollama",
        "endpoint": "http://localhost:11434",
        "model": "llama3.1:8b",
        "timeout_seconds": 60,
        "retries": 1,
        "context_length": 0,  # 0 == use backend/default estimate
        "temperature": 0.3,
        # Bound every completion, including map and reduce requests. This is
        # independent from the context-window reservation used for chunking.
        "max_tokens": 1024,
        # Ordinary summaries should not spend an unbounded hidden reasoning
        # budget. Supported backends receive this explicit control.
        "reasoning_effort": "none",
        # Cap on accumulated response bytes per request (32 MiB). A server
        # (or anything impersonating one) must not be able to grow this
        # process's memory without bound. 0 disables the cap.
        "max_response_bytes": 33554432,
    },
    "defaults": {
        "profile": "plain",
        "output_format": "markdown",
        "stream": True,
        "lang": None,
        "chunk_strategy": "auto",
    },
    "chunking": {
        "strategy": "auto",
        "max_tokens_per_chunk": 3000,
        "overlap_tokens": 200,
        "reserve_output_tokens": 1000,
        # Safety valve for very large documents: refuse to plan more chunks
        # than this rather than issuing an unbounded number of model calls.
        "max_chunks": 256,
    },
    "input": {
        "max_bytes": 52428800,  # 50 MiB
        "max_lines": 1000000,
        "encoding": "utf-8",
    },
    "session": {
        "enabled": False,
        "notes_dir": "~/.local/share/summarizer/sessions",
        "digest_format": "markdown",
        "append_mode": True,
    },
    "update": {
        "mode": "off",
        "channel": "stable",
        "check_on_start": False,
        "require_confirmation": True,
        "url": "",
        "public_key_path": "",
    },
}

# Environment variable -> (section, key) mapping.
_ENV_MAP: dict[str, tuple[str, str]] = {
    "SUMMARIZER_BACKEND": ("engine", "backend"),
    "SUMMARIZER_ENDPOINT": ("engine", "endpoint"),
    "SUMMARIZER_MODEL": ("engine", "model"),
    "SUMMARIZER_TIMEOUT": ("engine", "timeout_seconds"),
    "SUMMARIZER_RETRIES": ("engine", "retries"),
    "SUMMARIZER_MAX_TOKENS": ("engine", "max_tokens"),
    "SUMMARIZER_MAX_RESPONSE_BYTES": ("engine", "max_response_bytes"),
    "SUMMARIZER_REASONING_EFFORT": ("engine", "reasoning_effort"),
    "SUMMARIZER_PROFILE": ("defaults", "profile"),
    "SUMMARIZER_FORMAT": ("defaults", "output_format"),
    "SUMMARIZER_LANG": ("defaults", "lang"),
    "SUMMARIZER_CHUNK_STRATEGY": ("defaults", "chunk_strategy"),
    "SUMMARIZER_MAX_CHUNKS": ("chunking", "max_chunks"),
    "SUMMARIZER_MAX_BYTES": ("input", "max_bytes"),
    "SUMMARIZER_MAX_LINES": ("input", "max_lines"),
    "SUMMARIZER_ENCODING": ("input", "encoding"),
}


@dataclass(frozen=True)
class EngineSettings:
    backend: str = "ollama"
    endpoint: str = "http://localhost:11434"
    model: str = "llama3.1:8b"
    timeout_seconds: int = 60
    retries: int = 1
    context_length: int = 0
    temperature: float = 0.3
    max_tokens: int = 1024
    reasoning_effort: str = "none"
    # Cap on accumulated response bytes per HTTP exchange (0 disables).
    max_response_bytes: int = 33554432


@dataclass(frozen=True)
class DefaultsSettings:
    profile: str = "plain"
    output_format: str = "markdown"
    stream: bool = True
    lang: str | None = None
    chunk_strategy: str = "auto"


@dataclass(frozen=True)
class ChunkingSettings:
    strategy: str = "auto"
    max_tokens_per_chunk: int = 3000
    overlap_tokens: int = 200
    reserve_output_tokens: int = 1000
    # Upper bound on how many chunks a single document may be split into.
    max_chunks: int = 256


@dataclass(frozen=True)
class InputSettings:
    max_bytes: int = 52428800
    max_lines: int = 1000000
    encoding: str = "utf-8"


@dataclass(frozen=True)
class SessionSettings:
    enabled: bool = False
    notes_dir: str = "~/.local/share/summarizer/sessions"
    digest_format: str = "markdown"
    append_mode: bool = True


@dataclass(frozen=True)
class UpdateSettings:
    mode: str = "off"
    channel: str = "stable"
    check_on_start: bool = False
    require_confirmation: bool = True
    url: str = ""
    public_key_path: str = ""


@dataclass(frozen=True)
class Config:
    engine: EngineSettings = field(default_factory=EngineSettings)
    defaults: DefaultsSettings = field(default_factory=DefaultsSettings)
    chunking: ChunkingSettings = field(default_factory=ChunkingSettings)
    input: InputSettings = field(default_factory=InputSettings)
    session: SessionSettings = field(default_factory=SessionSettings)
    update: UpdateSettings = field(default_factory=UpdateSettings)
    # Paths of the config files that contributed values, for diagnostics.
    user_config_path: Path | None = None
    project_config_path: Path | None = None

    def with_overrides(self, **overrides: Any) -> "Config":
        """Return a copy with per-segment overrides applied.

        Supported overrides: `engine=EngineSettings(...)` (or a dict),
        `defaults=DefaultsSettings(...)`, `chunking=...`, `input=...`,
        `session=...`, `update=...`.
        """
        replacements: dict[str, Any] = {}
        for key, value in overrides.items():
            if key in ("engine", "defaults", "chunking", "input", "session", "update"):
                if value is not None:
                    if isinstance(value, dict):
                        current = getattr(self, key)
                        value = replace(current, **value)
                    replacements[key] = value
            else:
                replacements[key] = value
        return replace(self, **replacements)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def user_config_path() -> Path:
    env_override = os.environ.get("SUMMARIZER_CONFIG")
    if env_override:
        return Path(env_override).expanduser()
    config_home = os.environ.get("XDG_CONFIG_HOME")
    if config_home:
        return Path(config_home) / "summarizer" / "config.toml"
    return Path.home() / ".config" / "summarizer" / "config.toml"


def user_prompt_dir() -> Path:
    return user_config_path().parent / "prompts"


def project_config_path() -> Path:
    return Path.cwd() / "summarizer.toml"


def data_dir() -> Path:
    data_home = os.environ.get("XDG_DATA_HOME")
    if data_home:
        return Path(data_home) / "summarizer"
    return Path.home() / ".local" / "share" / "summarizer"


def sessions_dir() -> Path:
    return data_dir() / "sessions"


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _read_toml_file(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ConfigError(f"cannot read config file {path}: {exc}") from exc
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ConfigError(f"config file {path} is not valid UTF-8: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"config file {path} must contain a TOML table")
    return data


def _load_layer(path: Path) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    return _read_toml_file(path)


def load_env_layer() -> dict[str, Any]:
    layer: dict[str, Any] = {}
    for env_name, (section, key) in _ENV_MAP.items():
        value = os.environ.get(env_name)
        if value is None:
            continue
        layer.setdefault(section, {})[key] = _convert_env(env_name, value)
    return layer


def _convert_env(name: str, value: str) -> Any:
    if name in (
        "SUMMARIZER_TIMEOUT",
        "SUMMARIZER_RETRIES",
        "SUMMARIZER_MAX_TOKENS",
        "SUMMARIZER_MAX_BYTES",
        "SUMMARIZER_MAX_LINES",
    ):
        try:
            return int(value)
        except ValueError as exc:
            raise ConfigError(
                f"environment variable {name} must be an integer, got {value!r}"
            ) from exc
    return value


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge dicts; later layers win for scalar keys."""
    out = dict(base)
    for key, value in override.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out
def load_config(*, user_path: Path | None = None, project_path: Path | None = None) -> Config:
    """Resolve config from defaults, user file, project file, and environment."""
    user_path = user_path if user_path is not None else user_config_path()
    project_path = project_path if project_path is not None else project_config_path()

    merged: dict[str, Any] = dict(BUILTIN_DEFAULTS)
    user_layer = _load_layer(user_path)
    project_layer = _load_layer(project_path)
    env_layer = load_env_layer()
    merged = _deep_merge(merged, user_layer)
    merged = _deep_merge(merged, project_layer)
    merged = _deep_merge(merged, env_layer)

    def ctx(key: str) -> str:
        where = _find_key_location(key, user_layer, project_layer)
        return f"config key {key!r}" + (f" ({where})" if where else "")

    engine = EngineSettings(
        backend=_str(merged["engine"], "backend", ctx),
        endpoint=_str(merged["engine"], "endpoint", ctx),
        model=_str(merged["engine"], "model", ctx),
        timeout_seconds=_int(merged["engine"], "timeout_seconds", ctx, minimum=1),
        retries=_int(merged["engine"], "retries", ctx, minimum=0, maximum=10),
        context_length=_int(merged["engine"], "context_length", ctx, minimum=0),
        temperature=_float(merged["engine"], "temperature", ctx, minimum=0.0, maximum=2.0),
        max_tokens=_int(merged["engine"], "max_tokens", ctx, minimum=1),
        reasoning_effort=_reasoning_effort(merged["engine"], "reasoning_effort", ctx),
    )
    defaults = DefaultsSettings(
        profile=_str(merged["defaults"], "profile", ctx),
        output_format=_str(merged["defaults"], "output_format", ctx),
        stream=_bool(merged["defaults"], "stream", ctx),
        lang=_optional_str(merged["defaults"], "lang", ctx),
        chunk_strategy=_str(merged["defaults"], "chunk_strategy", ctx),
    )
    chunking = ChunkingSettings(
        strategy=_str(merged["chunking"], "strategy", ctx),
        max_tokens_per_chunk=_int(merged["chunking"], "max_tokens_per_chunk", ctx, minimum=1),
        overlap_tokens=_int(merged["chunking"], "overlap_tokens", ctx, minimum=0),
        reserve_output_tokens=_int(merged["chunking"], "reserve_output_tokens", ctx, minimum=0),
        max_chunks=_int(merged["chunking"], "max_chunks", ctx, minimum=1),
    )
    input_settings = InputSettings(
        max_bytes=_int(merged["input"], "max_bytes", ctx, minimum=1),
        max_lines=_int(merged["input"], "max_lines", ctx, minimum=1),
        encoding=_str(merged["input"], "encoding", ctx),
    )
    session = SessionSettings(
        enabled=_bool(merged["session"], "enabled", ctx),
        notes_dir=_str(merged["session"], "notes_dir", ctx),
        digest_format=_str(merged["session"], "digest_format", ctx),
        append_mode=_bool(merged["session"], "append_mode", ctx),
    )
    update = UpdateSettings(
        mode=_str(merged["update"], "mode", ctx),
        channel=_str(merged["update"], "channel", ctx),
        check_on_start=_bool(merged["update"], "check_on_start", ctx),
        require_confirmation=_bool(merged["update"], "require_confirmation", ctx),
        url=_str(merged["update"], "url", ctx),
        public_key_path=_str(merged["update"], "public_key_path", ctx),
    )

    return Config(
        engine,
        defaults,
        chunking,
        input_settings,
        session,
        update,
        user_config_path=user_path if user_path.is_file() else None,
        project_config_path=project_path if project_path.is_file() else None,
    )


def _find_key_location(key: str, user_layer: dict[str, Any], project_layer: dict[str, Any]) -> str | None:
    """Report the highest-precedence file that supplied this key."""
    for layer, label in ((project_layer, "project config"), (user_layer, "user config")):
        for section_values in layer.values():
            if isinstance(section_values, dict) and key in section_values:
                return label
    return None


# --- typed accessors ---------------------------------------------------------


def _type_error(key: str, expected: str, found: Any) -> ConfigError:
    return ConfigError(
        f"invalid value for {key}: expected {expected}, got "
        f"{type(found).__name__} ({found!r})"
    )


def _str(table: dict[str, Any], key: str, ctx) -> str:
    value = table[key]
    if not isinstance(value, str) or isinstance(value, bool):
        raise _type_error(ctx(key), "a string", value)
    return value


def _optional_str(table: dict[str, Any], key: str, ctx) -> str | None:
    value = table.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or isinstance(value, bool):
        raise _type_error(ctx(key), "a string", value)
    return value


def _int(table: dict[str, Any], key: str, ctx, *, minimum: int | None = None, maximum: int | None = None) -> int:
    value = table[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise _type_error(ctx(key), "an integer", value)
    if minimum is not None and value < minimum:
        raise ConfigError(f"{ctx(key)} must be >= {minimum}, got {value}")
    if maximum is not None and value > maximum:
        raise ConfigError(f"{ctx(key)} must be <= {maximum}, got {value}")
    return value


def _float(table: dict[str, Any], key: str, ctx, *, minimum: float | None = None, maximum: float | None = None) -> float:
    value = table[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _type_error(ctx(key), "a number", value)
    value = float(value)
    if minimum is not None and value < minimum:
        raise ConfigError(f"{ctx(key)} must be >= {minimum}, got {value}")
    if maximum is not None and value > maximum:
        raise ConfigError(f"{ctx(key)} must be <= {maximum}, got {value}")
    return value


def _bool(table: dict[str, Any], key: str, ctx) -> bool:
    value = table[key]
    if not isinstance(value, bool):
        raise _type_error(ctx(key), "a boolean", value)
    return value


def _reasoning_effort(table: dict[str, Any], key: str, ctx) -> str:
    value = _str(table, key, ctx).lower()
    allowed = ("none", "low", "medium", "high")
    if value not in allowed:
        raise ConfigError(
            f"{ctx(key)} must be one of: {', '.join(allowed)}; got {value!r}"
        )
    return value
