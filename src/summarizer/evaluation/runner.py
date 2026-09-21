"""Run evaluation cases against a local engine.

The runner is deliberately small and synchronous: one document, one engine,
mechanically-checked expectations. It never downloads anything, never talks
to a service other than the configured local endpoint, and never installs a
model. The same corpus can therefore be pointed at any backend/model by
changing ``--model``/``--backend``/``--endpoint``.

See :mod:`summarizer.evaluation.report` for the (non-objective) nature of the
checks.
"""

from __future__ import annotations

import json
import time
from typing import Sequence

from summarizer.chunking.token import estimate_tokens
from summarizer.config import Config
from summarizer.engine.base import Engine
from summarizer.errors import SummarizerError
from summarizer.evaluation.cases import Case, EvalPaths, with_chunking
from summarizer.evaluation.report import CaseResult, CheckResult, EvaluationReport
from summarizer.log import Diagnostics
from summarizer.pipeline import Pipeline, PipelineOptions

__all__ = ["run_case", "run_cases"]

# Allow a small overshoot on the bounded-output check: max_tokens is a hard
# cap on generated tokens, but our estimator is not the server's tokenizer.
_OUTPUT_TOLERANCE = 1.15


def _assertions_from_result(case: Case, result, *, engine: Engine) -> tuple[list[CheckResult], dict]:
    summary = result.summary
    if isinstance(summary, str):
        haystack = summary
    else:
        haystack = json.dumps(summary, ensure_ascii=False)
    lowered = haystack.lower()

    checks: list[CheckResult] = []

    if case.expect_json:
        valid = isinstance(summary, (dict, list))
        checks.append(
            CheckResult(
                "json_valid", valid,
                f"parsed as {type(summary).__name__}" if valid else "not a JSON object/array",
            )
        )
    else:
        non_empty = bool(haystack.strip())
        checks.append(
            CheckResult("non_empty", non_empty, f"{len(haystack)} chars" if non_empty else "empty output")
        )

    missing = [needle for needle in case.must_include if needle.lower() not in lowered]
    checks.append(
        CheckResult(
            "expected_content_present",
            not missing,
            "all sentinels present" if not missing else f"missing: {', '.join(missing)}",
        )
    )

    leaked = [needle for needle in case.must_not_include if needle.lower() in lowered]
    checks.append(
        CheckResult(
            "forbidden_content_absent",
            not leaked,
            "no injected marker echoed" if not leaked else f"echoed: {', '.join(leaked)}",
        )
    )

    if case.expect_strategy is not None:
        actual = result.aggregation.name
        checks.append(
            CheckResult("strategy_expected", actual == case.expect_strategy, f"strategy={actual}")
        )

    output_tokens = estimate_tokens(haystack)
    budget = int(getattr(engine, "max_tokens", 0) or 0)
    if budget:
        bounded = output_tokens <= budget * _OUTPUT_TOLERANCE
        checks.append(
            CheckResult("output_within_budget", bounded, f"~{output_tokens}/{budget} tokens")
        )

    metrics = {
        "latency_seconds": result.duration,
        "output_chars": len(haystack),
        "output_tokens_est": output_tokens,
        "strategy": result.aggregation.name,
        "chunks": len(result.chunks) if result.chunks else 1,
        "warnings": list(result.warnings),
    }
    return checks, metrics


def run_case(
    case: Case,
    *,
    engine: Engine,
    config: Config,
    diag: Diagnostics,
    paths: EvalPaths,
) -> CaseResult:
    """Run one case once and evaluate its mechanical properties."""
    fixture_path = paths.fixtures / case.fixture
    if not fixture_path.is_file():
        return CaseResult(
            id=case.id,
            description=case.description,
            tags=case.tags,
            passed=False,
            checks=[],
            error=f"fixture not found: {fixture_path}",
        )

    case_config = config.with_overrides(chunking=with_chunking(case, config.chunking))
    opts = PipelineOptions(
        profile=case.profile,
        output_format=case.output_format,
        model=engine.model,
        endpoint=engine.endpoint,
        stream=False,
    )
    pipeline = Pipeline(case_config, diag=diag, engine=engine)
    started = time.monotonic()
    try:
        result = pipeline.run(str(fixture_path), opts=opts)
    except SummarizerError as exc:
        return CaseResult(
            id=case.id,
            description=case.description,
            tags=case.tags,
            passed=False,
            checks=[],
            metrics={"latency_seconds": time.monotonic() - started},
            error=f"{type(exc).__name__}: {exc.message}",
        )
    except Exception as exc:  # never let one case abort the whole run
        return CaseResult(
            id=case.id,
            description=case.description,
            tags=case.tags,
            passed=False,
            checks=[],
            metrics={"latency_seconds": time.monotonic() - started},
            error=f"{type(exc).__name__}: {exc}",
        )

    checks, metrics = _assertions_from_result(case, result, engine=engine)
    return CaseResult(
        id=case.id,
        description=case.description,
        tags=case.tags,
        passed=all(check.passed for check in checks),
        checks=checks,
        metrics=metrics,
    )


def _merge_repeats(case: Case, runs: list[CaseResult]) -> CaseResult:
    """A case passes only when every repeat passed; metrics are aggregated."""
    if len(runs) == 1:
        return runs[0]
    failures = [run.error for run in runs if run.error]
    # Checks are identical in name/order across repeats; require all to pass.
    names: list[str] = []
    for run in runs:
        for check in run.checks:
            if check.name not in names:
                names.append(check.name)
    merged_checks: list[CheckResult] = []
    for name in names:
        per_repeat = [c for run in runs for c in run.checks if c.name == name]
        passed = bool(per_repeat) and all(c.passed for c in per_repeat)
        failing = [c.detail for c in per_repeat if not c.passed]
        merged_checks.append(
            CheckResult(name, passed, "; ".join(failing) if failing else (per_repeat[0].detail if per_repeat else ""))
        )
    latencies = [run.metrics.get("latency_seconds", 0.0) for run in runs]
    tokens = [run.metrics.get("output_tokens_est", 0) for run in runs]
    chars = [run.metrics.get("output_chars", 0) for run in runs]
    metrics = {
        "latency_seconds": sum(latencies) / len(latencies),
        "latency_seconds_min": min(latencies),
        "latency_seconds_max": max(latencies),
        "output_tokens_est": max(tokens),
        "output_chars": max(chars),
        "strategy": runs[0].metrics.get("strategy"),
        "chunks": max((run.metrics.get("chunks", 1) for run in runs), default=1),
        "repeats": len(runs),
    }
    return CaseResult(
        id=case.id,
        description=case.description,
        tags=case.tags,
        passed=not failures and all(check.passed for check in merged_checks),
        checks=merged_checks,
        metrics=metrics,
        error=failures[0] if failures else None,
        repeat_failures=failures,
    )


def run_cases(
    cases: Sequence[Case],
    *,
    engine: Engine,
    config: Config,
    diag: Diagnostics,
    paths: EvalPaths,
    repeats: int = 1,
) -> EvaluationReport:
    """Run every case, aggregating repeats, and return a structured report."""
    if repeats < 1:
        repeats = 1
    started = time.monotonic()
    results: list[CaseResult] = []
    for case in cases:
        diag.progress(f"evaluate: {case.id} ({case.fixture})")
        runs = [
            run_case(case, engine=engine, config=config, diag=diag, paths=paths)
            for _ in range(repeats)
        ]
        results.append(_merge_repeats(case, runs))
    return EvaluationReport(
        model=engine.model,
        backend=engine.backend,
        endpoint=engine.endpoint,
        repeats=repeats,
        duration_seconds=time.monotonic() - started,
        results=results,
    )
