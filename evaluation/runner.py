"""Re-export of the packaged evaluation runner (see summarizer.evaluation)."""

from __future__ import annotations

from summarizer.evaluation.runner import run_case, run_cases

__all__ = ["run_case", "run_cases"]
