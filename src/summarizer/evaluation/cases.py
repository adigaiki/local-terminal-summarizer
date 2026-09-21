"""Evaluation cases: declarative, deterministic, local-only.

A case pairs a fixture (a real document) with *mechanical* expectations and
metadata. It deliberately does **not** attempt a semantic quality score: the
only automated checks are structural/observable (did it run, is the JSON
valid, is the format respected, was a required sentinel retained, was an
injected marker echoed, which strategy ran, was the output bounded). Anything
resembling "faithfulness" is reported as a human-reviewed property, not a
number pretending to be ground truth.

Cases live in ``<eval-root>/cases/*.toml``. Fixtures live in
``<eval-root>/fixtures/``.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

from summarizer.errors import ConfigError

__all__ = [
    "Case",
    "load_cases",
    "filter_cases",
    "resolve_eval_root",
    "default_eval_root",
    "EvalPaths",
]

_VALID_FORMATS = ("plain", "markdown", "json")
_VALID_STRATEGIES = ("direct", "mapreduce")


@dataclass(frozen=True)
class Case:
    id: str
    description: str
    fixture: str
    profile: str = "plain"
    output_format: str = "markdown"
    tags: tuple[str, ...] = ()
    # Mechanical expectations. Empty tuples mean "not asserted".
    expect_json: bool = False
    must_include: tuple[str, ...] = ()
    must_not_include: tuple[str, ...] = ()
    expect_strategy: str | None = None
    # Optional per-case document sizing so long-document behaviour can be
    # exercised against a small local model without huge fixtures/costs.
    max_tokens_per_chunk: int | None = None
    reserve_output_tokens: int | None = None
    max_chunks: int | None = None
    # Free-form notes shown in the report; never an automated score.
    notes: str = field(default="")


@dataclass(frozen=True)
class EvalPaths:
    root: Path
    fixtures: Path
    cases: Path


def _repository_root() -> Path:
    # src/summarizer/evaluation/cases.py -> repository root.
    return Path(__file__).resolve().parents[3]


def _candidate_roots() -> list[Path]:
    candidates: list[Path] = []
    env = os.environ.get("SUMMARIZER_EVAL_DIR")
    if env:
        candidates.append(Path(env).expanduser())
    candidates.append(Path.cwd() / "evaluation")
    candidates.append(_repository_root() / "evaluation")
    return candidates


def default_eval_root() -> Path | None:
    """First candidate directory that actually contains a ``cases`` folder."""
    for candidate in _candidate_roots():
        if (candidate / "cases").is_dir():
            return candidate
    return None


def resolve_eval_root(explicit: str | Path | None = None) -> EvalPaths:
    """Resolve the fixtures/cases root, with a clear error when absent."""
    if explicit is not None:
        root = Path(explicit).expanduser()
        if not (root / "cases").is_dir():
            raise ConfigError(
                f"evaluation directory {root} has no cases/ subdirectory",
                hint="pass --eval-dir pointing at a directory containing cases/ and fixtures/",
            )
    else:
        root = default_eval_root()
        if root is None:
            raise ConfigError(
                "could not find an evaluation directory",
                hint="run from the repository root, or set --eval-dir / "
                     "$SUMMARIZER_EVAL_DIR to a directory containing cases/",
            )
    return EvalPaths(root=root, fixtures=root / "fixtures", cases=root / "cases")


def _as_str_tuple(value: object, *, where: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(value)
    raise ConfigError(f"{where} must be a string or a list of strings")


def _case_from_table(table: dict[str, object], *, path: Path) -> Case:
    where = f"case in {path}"
    case_id = table.get("id")
    if not isinstance(case_id, str) or not case_id.strip():
        raise ConfigError(f"{where}: every case needs a non-empty 'id'")
    fixture = table.get("fixture")
    if not isinstance(fixture, str) or not fixture.strip():
        raise ConfigError(f"{where}: case {case_id!r} needs a 'fixture'")

    output_format = str(table.get("output_format", "markdown")).lower()
    if output_format not in _VALID_FORMATS:
        raise ConfigError(
            f"{where}: case {case_id!r} has unknown output_format {output_format!r}",
            hint=f"expected one of: {', '.join(_VALID_FORMATS)}",
        )
    expect_strategy = table.get("expect_strategy")
    if expect_strategy is not None and expect_strategy not in _VALID_STRATEGIES:
        raise ConfigError(
            f"{where}: case {case_id!r} has unknown expect_strategy {expect_strategy!r}",
            hint=f"expected one of: {', '.join(_VALID_STRATEGIES)}",
        )

    def _opt_int(key: str) -> int | None:
        value = table.get(key)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ConfigError(f"{where}: {key} must be a positive integer")
        return value

    return Case(
        id=case_id,
        description=str(table.get("description", "")),
        fixture=fixture,
        profile=str(table.get("profile", "plain")),
        output_format=output_format,
        tags=_as_str_tuple(table.get("tags"), where=f"{where}: tags"),
        expect_json=bool(table.get("expect_json", output_format == "json")),
        must_include=_as_str_tuple(table.get("must_include"), where=f"{where}: must_include"),
        must_not_include=_as_str_tuple(table.get("must_not_include"), where=f"{where}: must_not_include"),
        expect_strategy=expect_strategy,
        max_tokens_per_chunk=_opt_int("max_tokens_per_chunk"),
        reserve_output_tokens=_opt_int("reserve_output_tokens"),
        max_chunks=_opt_int("max_chunks"),
        notes=str(table.get("notes", "")),
    )


def load_cases(paths: EvalPaths) -> list[Case]:
    """Load every ``*.toml`` case file, sorted by filename then case id."""
    cases: list[Case] = []
    files = sorted(paths.cases.glob("*.toml"))
    if not files:
        raise ConfigError(
            f"no case files (*.toml) in {paths.cases}",
            hint="add at least one [[case]] table",
        )
    for path in files:
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"invalid TOML in {path}: {exc}") from exc
        tables = data.get("case")
        if not isinstance(tables, list) or not tables:
            raise ConfigError(f"{path}: expected one or more [[case]] tables")
        for table in tables:
            if not isinstance(table, dict):
                raise ConfigError(f"{path}: each [[case]] must be a table")
            cases.append(_case_from_table(table, path=path))
    seen: set[str] = set()
    duplicates: set[str] = set()
    for case in cases:
        if case.id in seen:
            duplicates.add(case.id)
        seen.add(case.id)
    if duplicates:
        raise ConfigError(
            f"duplicate case ids: {', '.join(sorted(duplicates))}",
            hint="case ids must be unique across all case files",
        )
    return cases


def filter_cases(
    cases: list[Case],
    *,
    ids: list[str] | None = None,
    tags: list[str] | None = None,
) -> list[Case]:
    """Select a subset by id and/or tag; unknown ids are an error."""
    selected = cases
    if ids:
        wanted = set(ids)
        known = {case.id for case in cases}
        unknown = wanted - known
        if unknown:
            raise ConfigError(
                f"unknown evaluation case id(s): {', '.join(sorted(unknown))}",
                hint=f"known cases: {', '.join(sorted(known))}",
            )
        selected = [case for case in selected if case.id in wanted]
    if tags:
        wanted_tags = set(tags)
        selected = [case for case in selected if wanted_tags & set(case.tags)]
    return selected


def with_chunking(case: Case, chunking):
    """Apply a case's optional chunking overrides to a ChunkingSettings."""
    updates: dict[str, int] = {}
    if case.max_tokens_per_chunk is not None:
        updates["max_tokens_per_chunk"] = case.max_tokens_per_chunk
    if case.reserve_output_tokens is not None:
        updates["reserve_output_tokens"] = case.reserve_output_tokens
    if case.max_chunks is not None:
        updates["max_chunks"] = case.max_chunks
    return replace(chunking, **updates) if updates else chunking
