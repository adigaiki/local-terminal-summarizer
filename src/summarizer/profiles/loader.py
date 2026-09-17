"""Profile loading and validation.

Profiles are plain-text/Markdown templates. A profile may use these
placeholders:

    {{input}}          document content (required for summarization profiles)
    {{summaries}}       interim chunk summaries (required for reduce profiles)
    {{context}}         optional trusted supplemental context
    {{lang}}            target language
    {{output_format}}   requested output format (plain/markdown/json)

Resolution: a user profile with the same name overrides the built-in one.
User profiles live in `~/.config/summarizer/prompts/`; built-ins ship in
`<package>/profiles/prompts/`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from summarizer.errors import ConfigError
from summarizer.log import Diagnostics

__all__ = ["Profile", "load_profile", "list_profiles", "BUILTIN_PROFILES", "REDUCE_PROFILE", "PROMPT_DIR"]

PROMPT_DIR = Path(__file__).parent / "prompts"
BUILTIN_PROFILES = ("plain", "code", "academic", "meeting")
REDUCE_PROFILE = "__reduce__"
PLACEHOLDERS = ("{{input}}", "{{summaries}}")


@dataclass(frozen=True)
class Profile:
    name: str
    text: str
    source: str  # "builtin" | "user" | "project"
    path: Path | None = None

    @property
    def is_reduce(self) -> bool:
        return "{{summaries}}" in self.text

    @property
    def has_input_slot(self) -> bool:
        return "{{input}}" in self.text


def validate_profile(name: str, text: str, path: Path | None = None) -> None:
    if not text.strip():
        raise ConfigError(
            f"profile {name!r} is empty {_where(path)}",
            hint="prompt profiles must be non-empty",
        )
    if not any(p in text for p in PLACEHOLDERS):
        raise ConfigError(
            f"profile {name!r} lacks a content placeholder {_where(path)}",
            hint=f"add one of: {', '.join(PLACEHOLDERS)}",
        )
    if "{{input}}" in text and "{{summaries}}" in text:
        raise ConfigError(
            f"profile {name!r} mixes {{{{input}}}} and {{{{summaries}}}} {_where(path)}",
            hint="use exactly one content placeholder per profile",
        )
    for placeholder in PLACEHOLDERS:
        count = text.count(placeholder)
        if count > 1:
            raise ConfigError(
                f"profile {name!r} contains {placeholder} {count} times {_where(path)}",
                hint="use exactly one content placeholder per profile",
            )


def _where(path: Path | None) -> str:
    return f" ({path})" if path else ""


def _builtin_path(name: str) -> Path:
    return PROMPT_DIR / f"{name}.md"


def _read_profile(name: str, path: Path) -> Profile:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read profile {name!r} at {path}: {exc}") from exc
    validate_profile(name, text, path)
    source = "builtin" if Path(path).parent == PROMPT_DIR else "user"
    return Profile(name=name, text=text, source=source, path=path)


def load_profile(
    name: str,
    *,
    user_dir: Path | None = None,
    diag: Diagnostics | None = None,
    reduce: bool = False,
) -> Profile:
    """Resolve a profile by name: user override first, then built-in.

    ``reduce=True`` is a small compatibility/convenience selector for the
    built-in reduction profile.  Map-reduce always uses that dedicated
    profile rather than treating a normal summarization profile as reduction
    instructions.
    """
    if reduce:
        name = REDUCE_PROFILE
    if "/" in name or "\0" in name or name in (".", ".."):
        raise ConfigError(f"invalid profile name {name!r}",
                          hint="profile names must be simple file names")

    if user_dir is not None:
        for candidate in (user_dir / f"{name}.md", user_dir / name):
            if candidate.is_file():
                return _read_profile(name, candidate)

    builtin = _builtin_path(name)
    if builtin.is_file():
        return _read_profile(name, builtin)

    if diag is not None:
        diag.verbose_message(f"profile {name!r} not found in user or built-in prompts")
    raise ConfigError(
        f"unknown profile {name!r}",
        hint=f"available: {', '.join(list_profiles(user_dir=user_dir))}",
    )


def list_profiles(*, user_dir: Path | None = None) -> list[str]:
    names: set[str] = set()
    for path in sorted(PROMPT_DIR.glob("*.md")):
        if path.stem != REDUCE_PROFILE:
            names.add(path.stem)
    if user_dir is not None and user_dir.is_dir():
        for path in sorted(user_dir.iterdir()):
            if path.is_file() and not path.name.startswith("."):
                names.add(path.stem)
    return sorted(names)
