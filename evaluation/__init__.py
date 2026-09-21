"""Repository-level evaluation harness (fixtures + cases live here).

The executable code lives in :mod:`summarizer.evaluation` so that
``summarize evaluate`` works from an installed package; the modules in this
directory are thin re-exports so the layout also reads naturally as a
standalone harness.

    evaluation/
      fixtures/      documents used by cases
      cases/         case definitions (*.toml)
      runner.py      re-export of summarizer.evaluation.runner
      report.py      re-export of summarizer.evaluation.report

Run it with:

    summarize evaluate --dry-run
    summarize evaluate --model qwen3:8b
"""

from __future__ import annotations

__all__: list[str] = []
