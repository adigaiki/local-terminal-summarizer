"""Re-export of the packaged evaluation report (see summarizer.evaluation)."""

from __future__ import annotations

from summarizer.evaluation.report import (
    DISCLAIMER,
    SCHEMA,
    CaseResult,
    CheckResult,
    EvaluationReport,
    render_text,
)

__all__ = [
    "CheckResult",
    "CaseResult",
    "EvaluationReport",
    "SCHEMA",
    "DISCLAIMER",
    "render_text",
]
