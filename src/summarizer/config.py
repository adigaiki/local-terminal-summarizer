"""Configuration loading with documented precedence.

Precedence (highest wins):

    CLI  ->  environment  ->  project config  ->  user config  ->  built-in defaults

CLI overrides are applied by cli.py on top of the resolved :class:`Config`.
User config lives at `~/.config/summarizer/config.toml` (or
`$XDG_CONFIG_HOME/summarizer/config.toml`); `$SUMMARIZER_CONFIG` overrides that
path.

Project config is **opt-in**. `./summarizer.toml` is read only when the
environment asks for it, so a cloned repository cannot silently force a
surprising model, endpoint or profile on you:

  * `SUMMARIZER_PROJECT_CONFIG=1`      -> read `./summarizer.toml` if present
  * `SUMMARIZER_PROJECT_CONFIG=/path`  -> read that file
  * unset                              -> no project config is read

When a project config is read it keeps its documented place in the precedence
chain. Each resolved value records which layer supplied it, so `--dry-run`
and `summarize doctor` can show the source without ever printing a secret.

Malformed TOML and type errors raise :class:`ConfigError` with the file path
and, where available, line/key information. A user's config is never
overwritten or mutated.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

from summarizer.errors import ConfigError

__all__ = [
    "Config",
    "EngineSettings",
    "DefaultsSettings",
    "ChunkingSettings",
    "InputSettings",
    "SessionSettings",
    "UpdateSettings",
    "CacheSettings",
    "load_config",
    "user_config_path",
    "user_prompt_dir",
    "project_config_path",
    "project_config_requested",
    "data_dir",
    "sessions_dir",
    "cache_dir",
    "MAX_CONCURRENCY",
    "SOURCE_BUILTIN",
    "SOURCE_USER",
    "SOURCE_PROJECT",
    "SOURCE_ENV",
    "SOURCE_CLI",
]

ENV_PREFIX = "SUMMARIZER_"

# Human-readable labels for the resolved-configuration sources.
SOURCE_BUILTIN = "built-in defaults"
SOURCE_USER = "user config"
SOURCE_PROJECT = "project config"
SOURCE_ENV = "environment"
SOURCE_CLI = "command line"

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
        # Interactive progress on stderr. Always suppressed by --quiet and by
        # a non-TTY stderr; never written to stdout.
        "progress": True,
    },
    "chunking": {
        "strategy": "auto",
        "max_tokens_per_chunk": 3000,
        "overlap_tokens": 200,
        "reserve_output_tokens": 1000,
        # Safety valve for very large documents: refuse to plan more chunks
        # than this rather than issuing an unbounded number of model calls.
        "max_chunks": 256,
        # Bounded parallelism for independent map-stage chunks. 1 preserves
        # the sequential behaviour exactly. Hard-capped at MAX_CONCURRENCY.
        "concurrency": 1,
    },
    "input": {
        "max_bytes": 52428800,  # 50 MiB
        "max_lines": 1000000,
        "encoding": "utf-8",
        "max_pdf_pages": 2000,
        "max_extracted_bytes": 33554432,
        "ocr_timeout_seconds": 120,
    },
    "session": {
        # Master switch. Recording still requires an explicitly selected
        # session (--session, $SUMMARIZER_SESSION, or the same-directory rule).
        "enabled": True,
        "notes_dir": "~/.local/share/summarizer/sessions",
        "digest_format": "markdown",  # markdown, json, or both
        # Warn (never auto-close) about sessions open longer than this.
        "max_age_hours": 24,
    },
    "update": {
        "mode": "off",
        "channel": "stable",
        "check_on_start": False,
        "require_confirmation": True,
        "url": "",
        "public_key_path": "",
    },
    "cache": {
        # off | read | write | readwrite. Off by default: no local state is
        # created unless the user opts in. Only derived summaries and hashes
        # are stored, never document contents.
        "mode": "off",
        "dir": "~/.cache/summarizer",
        # Bounds so the cache can never grow without limit.
        "max_entries": 500,
        "max_bytes": 268435456,  # 256 MiB
        "max_chunk_bytes": 1048576,  # skip a single oversized result (1 MiB)
    },
}

# Hard ceiling for [chunking] concurrency. Bounded so a configuration mistake
# cannot spawn an unbounded number of worker threads against a local server.
MAX_CONCURRENCY = 8

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
    "SUMMARIZER_REASONING": ("engine", "reasoning"),
    "SUMMARIZER_PROFILE": ("defaults", "profile"),
    "SUMMARIZER_FORMAT": ("defaults", "output_format"),
    "SUMMARIZER_LANG": ("defaults", "lang"),
    "SUMMARIZER_CHUNK_STRATEGY": ("defaults", "chunk_strategy"),
    "SUMMARIZER_MAX_CHUNKS": ("chunking", "max_chunks"),
    "SUMMARIZER_CONCURRENCY": ("chunking", "concurrency"),
    "SUMMARIZER_PROGRESS": ("defaults", "progress"),
    "SUMMARIZER_CACHE": ("cache", "mode"),
    "SUMMARIZER_CACHE_DIR": ("cache", "dir"),
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
    progress: bool = True


@dataclass(frozen=True)
class ChunkingSettings:
    strategy: str = "auto"
    max_tokens_per_chunk: int = 3000
    overlap_tokens: int = 200
    reserve_output_tokens: int = 1000
    # Upper bound on how many chunks a single document may be split into.
    max_chunks: int = 256
    # Bounded map-stage parallelism; 1 keeps the original sequential behavior.
    concurrency: int = 1


@dataclass(frozen=True)
class InputSettings:
    max_bytes: int = 52428800
    max_lines: int = 1000000
    encoding: str = "utf-8"
    # PDF/OCR resource limits (see README "Resource limits").
    max_pdf_pages: int = 2000
    max_extracted_bytes: int = 33554432
    ocr_timeout_seconds: int = 120


@dataclass(frozen=True)
class SessionSettings:
    """Session recording. Nothing is stored unless a session is selected."""
    enabled: bool = True
    notes_dir: str = "~/.local/share/summarizer/sessions"
    digest_format: str = "markdown"
    max_age_hours: float = 24.0


@dataclass(frozen=True)
class UpdateSettings:
    mode: str = "off"
    channel: str = "stable"
    check_on_start: bool = False
    require_confirmation: bool = True
    url: str = ""
    public_key_path: str = ""


@dataclass(frozen=True)
class CacheSettings:
    """Optional local cache for reusable intermediate (map) results.

    Off by default. When enabled it stores derived summaries plus hashes that
    make entries self-invalidating; it never stores the document itself.
    """

    mode: str = "off"  # off | read | write | readwrite
    dir: str = "~/.cache/summarizer"
    max_entries: int = 500
    max_bytes: int = 268435456
    max_chunk_bytes: int = 1048576

    @property
    def read_enabled(self) -> bool:
        return self.mode in ("read", "readwrite")

    @property
    def write_enabled(self) -> bool:
        return self.mode in ("write", "readwrite")


@dataclass(frozen=True)
class Config:
    engine: EngineSettings = field(default_factory=EngineSettings)
    defaults: DefaultsSettings = field(default_factory=DefaultsSettings)
    chunking: ChunkingSettings = field(default_factory=ChunkingSettings)
    input: InputSettings = field(default_factory=InputSettings)
    session: SessionSettings = field(default_factory=SessionSettings)
    update: UpdateSettings = field(default_factory=UpdateSettings)
    cache: CacheSettings = field(default_factory=CacheSettings)
    # Paths of the config files that contributed values, for diagnostics.
    user_config_path: Path | None = None
    project_config_path: Path | None = None
    # Which layer supplied each dotted key (e.g. "engine.model"). Labels are
    # SOURCE_* constants plus the concrete file path for config files. Never
    # contains a value, so it is safe to print.
    origins: Mapping[str, str] = field(default_factory=dict)

    def origin(self, key: str) -> str:
        """The layer that supplied ``key`` (``"engine.model"``), if known."""
        return self.origins.get(key, SOURCE_BUILTIN)

    def with_origins(self, updates: Mapping[str, str]) -> "Config":
        """Return a copy with origin labels overridden (e.g. by the CLI)."""
        if not updates:
            return self
        merged = {**dict(self.origins), **dict(updates)}
        return replace(self, origins=merged)

    def with_overrides(self, **overrides: Any) -> "Config":
        """Return a copy with per-segment overrides applied.

        Supported overrides: `engine=EngineSettings(...)` (or a dict),
        `defaults=DefaultsSettings(...)`, `chunking=...`, `input=...`,
        `session=...`, `update=...`.
        """
        replacements: dict[str, Any] = {}
        for key, value in overrides.items():
            if key in ("engine", "defaults", "chunking", "input", "session", "update", "cache"):
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
    """The conventional project config location (whether or not it is read)."""
    return Path.cwd() / "summarizer.toml"


_PROJECT_TRUTHY = frozenset({"1", "true", "yes", "on"})


def project_config_requested() -> Path | None:
    """Resolve the opt-in project config path.

    Project config is opt-in so that cloning a repository cannot silently
    change your model, endpoint or profile. `SUMMARIZER_PROJECT_CONFIG` may be
    a truthy flag (read `./summarizer.toml` when present) or an explicit path.
    Returns ``None`` when the user has not opted in.
    """
    value = os.environ.get("SUMMARIZER_PROJECT_CONFIG", "").strip()
    if not value:
        return None
    if value.lower() in _PROJECT_TRUTHY:
        return project_config_path()
    return Path(value).expanduser()


def data_dir() -> Path:
    data_home = os.environ.get("XDG_DATA_HOME")
    if data_home:
        return Path(data_home) / "summarizer"
    return Path.home() / ".local" / "share" / "summarizer"


def sessions_dir() -> Path:
    return data_dir() / "sessions"


def cache_dir() -> Path:
    """Default XDG cache location for the optional local cache."""
    cache_home = os.environ.get("XDG_CACHE_HOME")
    if cache_home:
        return Path(cache_home) / "summarizer"
    return Path.home() / ".cache" / "summarizer"


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
    if name == "SUMMARIZER_PROGRESS":
        return value.strip().lower() in ("1", "true", "yes", "on")
    if name in (
        "SUMMARIZER_TIMEOUT",
        "SUMMARIZER_RETRIES",
        "SUMMARIZER_MAX_TOKENS",
        "SUMMARIZER_MAX_RESPONSE_BYTES",
        "SUMMARIZER_MAX_BYTES",
        "SUMMARIZER_MAX_LINES",
        "SUMMARIZER_MAX_CHUNKS",
        "SUMMARIZER_CONCURRENCY",
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


def _canonicalize_layer(layer: dict[str, Any], *, label: str) -> dict[str, Any]:
    """Normalise within a single layer before merging.

    ``reasoning`` is accepted as a generic alias for ``reasoning_effort`` so
    the documented ``[engine] reasoning = "none"`` spelling works. Setting
    both to different values in the same layer is a configuration error.
    """
    engine = layer.get("engine")
    if isinstance(engine, dict) and "reasoning" in engine:
        alias = engine.pop("reasoning")
        if "reasoning_effort" in engine and engine["reasoning_effort"] != alias:
            raise ConfigError(
                f"{label} sets both [engine] reasoning and reasoning_effort "
                "to different values",
                hint="set only one of them",
            )
        engine["reasoning_effort"] = alias
    return layer


def _layer_has(layer: dict[str, Any], section: str, key: str) -> bool:
    values = layer.get(section)
    return isinstance(values, dict) and key in values


def _layer_label(kind: str, path: Path | None) -> str:
    if path is None:
        return SOURCE_BUILTIN
    return f"{kind} ({path})"


def _compute_origins(
    *,
    env_layer: dict[str, Any],
    project_layer: dict[str, Any],
    user_layer: dict[str, Any],
    project_label: str,
    user_label: str,
) -> dict[str, str]:
    """Map dotted keys to the highest-precedence layer that supplied them."""
    origins: dict[str, str] = {}
    # Highest precedence first; setdefault keeps the winner.
    ordered = [
        (SOURCE_ENV, env_layer),
        (project_label, project_layer),
        (user_label, user_layer),
        (SOURCE_BUILTIN, BUILTIN_DEFAULTS),
    ]
    for label, layer in ordered:
        for section, values in layer.items():
            if not isinstance(values, dict):
                continue
            for key in values:
                origins.setdefault(f"{section}.{key}", label)
    return origins


def load_config(*, user_path: Path | None = None, project_path: Path | None = None) -> Config:
    """Resolve config from defaults, user file, project file, and environment.

    ``project_path`` defaults to :func:`project_config_requested`: project
    config is opt-in, so without ``SUMMARIZER_PROJECT_CONFIG`` no project file
    is read and a cloned repository cannot change your engine settings.
    """
    user_path = user_path if user_path is not None else user_config_path()
    if project_path is None:
        project_path = project_config_requested()

    user_layer = _canonicalize_layer(_load_layer(user_path), label="user config")
    project_layer = _canonicalize_layer(_load_layer(project_path), label="project config")
    env_layer = _canonicalize_layer(load_env_layer(), label="environment")

    # Endpoint discovery: a backend's documented loopback default is used
    # only when no layer ever configured an endpoint, so switching backend
    # without setting an endpoint lands on the right local port.
    endpoint_explicit = any(
        _layer_has(layer, "engine", "endpoint")
        for layer in (env_layer, project_layer, user_layer)
    )

    merged: dict[str, Any] = dict(BUILTIN_DEFAULTS)
    merged = _deep_merge(merged, user_layer)
    merged = _deep_merge(merged, project_layer)
    merged = _deep_merge(merged, env_layer)

    if not endpoint_explicit:
        # Imported lazily: the engine package imports this module through the
        # factory, so a module-level import here would be circular.
        from summarizer.engine.backends import (
            canonical_backend_name,
            default_endpoint_for_backend,
        )

        backend = merged["engine"].get("backend")
        if isinstance(backend, str):
            default_endpoint = default_endpoint_for_backend(canonical_backend_name(backend))
            if default_endpoint:
                merged["engine"]["endpoint"] = default_endpoint

    def ctx(key: str) -> str:
        where = _find_key_location(key, env_layer, project_layer, user_layer)
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
        max_response_bytes=_int(merged["engine"], "max_response_bytes", ctx, minimum=0),
    )
    defaults = DefaultsSettings(
        profile=_str(merged["defaults"], "profile", ctx),
        output_format=_str(merged["defaults"], "output_format", ctx),
        stream=_bool(merged["defaults"], "stream", ctx),
        lang=_optional_str(merged["defaults"], "lang", ctx),
        chunk_strategy=_str(merged["defaults"], "chunk_strategy", ctx),
        progress=_bool(merged["defaults"], "progress", ctx),
    )
    chunking = ChunkingSettings(
        strategy=_str(merged["chunking"], "strategy", ctx),
        max_tokens_per_chunk=_int(merged["chunking"], "max_tokens_per_chunk", ctx, minimum=1),
        overlap_tokens=_int(merged["chunking"], "overlap_tokens", ctx, minimum=0),
        reserve_output_tokens=_int(merged["chunking"], "reserve_output_tokens", ctx, minimum=0),
        max_chunks=_int(merged["chunking"], "max_chunks", ctx, minimum=1),
        concurrency=_int(merged["chunking"], "concurrency", ctx, minimum=1, maximum=MAX_CONCURRENCY),
    )
    input_settings = InputSettings(
        max_bytes=_int(merged["input"], "max_bytes", ctx, minimum=1),
        max_lines=_int(merged["input"], "max_lines", ctx, minimum=1),
        encoding=_str(merged["input"], "encoding", ctx),
        max_pdf_pages=_int(merged["input"], "max_pdf_pages", ctx, minimum=1, maximum=10000),
        max_extracted_bytes=_int(merged["input"], "max_extracted_bytes", ctx, minimum=1, maximum=536870912),
        ocr_timeout_seconds=_int(merged["input"], "ocr_timeout_seconds", ctx, minimum=1, maximum=600),
    )
    session = SessionSettings(
        enabled=_bool(merged["session"], "enabled", ctx),
        notes_dir=_str(merged["session"], "notes_dir", ctx),
        digest_format=_str(merged["session"], "digest_format", ctx),
        max_age_hours=_float(merged["session"], "max_age_hours", ctx, minimum=0.0, maximum=8760.0),
    )
    update = UpdateSettings(
        mode=_str(merged["update"], "mode", ctx),
        channel=_str(merged["update"], "channel", ctx),
        check_on_start=_bool(merged["update"], "check_on_start", ctx),
        require_confirmation=_bool(merged["update"], "require_confirmation", ctx),
        url=_str(merged["update"], "url", ctx),
        public_key_path=_str(merged["update"], "public_key_path", ctx),
    )
    cache = CacheSettings(
        mode=_cache_mode(merged["cache"], "mode", ctx),
        dir=_str(merged["cache"], "dir", ctx),
        max_entries=_int(merged["cache"], "max_entries", ctx, minimum=1, maximum=1000000),
        max_bytes=_int(merged["cache"], "max_bytes", ctx, minimum=0),
        max_chunk_bytes=_int(merged["cache"], "max_chunk_bytes", ctx, minimum=0),
    )

    origins = _compute_origins(
        env_layer=env_layer,
        project_layer=project_layer,
        user_layer=user_layer,
        project_label=_layer_label(SOURCE_PROJECT, project_path if project_layer else None),
        user_label=_layer_label(SOURCE_USER, user_path if user_layer else None),
    )
    # `endpoint` may have been filled from a backend default rather than a
    # layer; record that honestly instead of claiming the built-in default.
    if not endpoint_explicit:
        origins["engine.endpoint"] = SOURCE_BUILTIN

    return Config(
        engine,
        defaults,
        chunking,
        input_settings,
        session,
        update,
        cache,
        user_config_path=user_path if user_path and user_path.is_file() else None,
        project_config_path=project_path if project_path and project_path.is_file() else None,
        origins=origins,
    )


def _find_key_location(
    key: str,
    env_layer: dict[str, Any],
    project_layer: dict[str, Any],
    user_layer: dict[str, Any],
) -> str | None:
    """Report the highest-precedence layer that supplied this key."""
    for layer, label in (
        (env_layer, "environment"),
        (project_layer, "project config"),
        (user_layer, "user config"),
    ):
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


_CACHE_MODE_ALIASES = {
    "off": "off",
    "none": "off",
    "false": "off",
    "read": "read",
    "write": "write",
    "readwrite": "readwrite",
    "on": "readwrite",
    "true": "readwrite",
    "yes": "readwrite",
}


def _cache_mode(table: dict[str, Any], key: str, ctx) -> str:
    value = _str(table, key, ctx).lower()
    if value not in _CACHE_MODE_ALIASES:
        raise ConfigError(
            f"{ctx(key)} must be one of: off, read, write, readwrite; got {value!r}"
        )
    return _CACHE_MODE_ALIASES[value]
