"""Local evaluation harness.

Run the same deterministic corpus against different local models/backends:

    summarize evaluate --model qwen3:8b
    summarize evaluate --dry-run
    summarize evaluate --format json

The checks are mechanical (validity, format, sentinel retention, injected
marker absence, strategy, output bounds). They are deliberately not an
objective quality score and no external judge model is used.
"""

from summarizer.evaluation.cases import (
    Case,
    EvalPaths,
    default_eval_root,
    filter_cases,
    load_cases,
    resolve_eval_root,
)
from summarizer.evaluation.report import (
    DISCLAIMER,
    SCHEMA,
    CaseResult,
    CheckResult,
    EvaluationReport,
    render_text,
)
from summarizer.evaluation.runner import run_case, run_cases

__all__ = [
    "Case",
    "EvalPaths",
    "CheckResult",
    "CaseResult",
    "EvaluationReport",
    "SCHEMA",
    "DISCLAIMER",
    "load_cases",
    "filter_cases",
    "resolve_eval_root",
    "default_eval_root",
    "run_case",
    "run_cases",
    "render_text",
]
