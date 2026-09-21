"""Structured evaluation reports.

The report is deliberately explicit about what it is *not*: the boolean
checks are mechanical properties (format valid, sentinel retained, injected
marker absent, strategy as expected, output bounded). They are not an
objective quality score, and no external judge model is involved. Metrics
such as latency and estimated output tokens are observations, reported as
such.

Schema id: ``summarizer.evaluation.report.v1``.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

__all__ = [
    "CheckResult",
    "CaseResult",
    "EvaluationReport",
    "SCHEMA",
    "DISCLAIMER",
]

SCHEMA = "summarizer.evaluation.report.v1"

DISCLAIMER = (
    "Mechanical checks only: validity, format, sentinel retention, injected-"
    "marker absence, strategy and output bounds. These are not objective "
    "quality scores and no external judge model was used."
)


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class CaseResult:
    id: str
    description: str
    tags: tuple[str, ...]
    passed: bool
    checks: list[CheckResult]
    metrics: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    repeat_failures: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "tags": list(self.tags),
            "passed": self.passed,
            "checks": [asdict(check) for check in self.checks],
            "metrics": self.metrics,
            "error": self.error,
            "repeat_failures": list(self.repeat_failures),
        }


@dataclass
class EvaluationReport:
    model: str
    backend: str
    endpoint: str
    repeats: int
    duration_seconds: float
    results: list[CaseResult]

    @property
    def passed(self) -> bool:
        return all(result.passed for result in self.results)

    @property
    def passed_count(self) -> int:
        return sum(1 for result in self.results if result.passed)

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "backend": self.backend,
            "model": self.model,
            "endpoint": self.endpoint,
            "repeats": self.repeats,
            "duration_seconds": round(self.duration_seconds, 3),
            "summary": {
                "cases": len(self.results),
                "passed": self.passed_count,
                "failed": len(self.results) - self.passed_count,
            },
            "results": [result.to_json() for result in self.results],
            "disclaimer": DISCLAIMER,
        }

    def to_json_text(self) -> str:
        return json.dumps(self.to_json(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def render_text(report: EvaluationReport) -> str:
    lines = [
        "Evaluation report (mechanical checks, not a quality score)",
        "",
        f"Backend:  {report.backend}",
        f"Model:    {report.model}",
        f"Endpoint: {report.endpoint}",
        f"Repeats:  {report.repeats}",
        "",
    ]
    for result in report.results:
        glyph = "PASS" if result.passed else "FAIL"
        lines.append(f"[{glyph}] {result.id} — {result.description}")
        for check in result.checks:
            mark = "ok " if check.passed else "BAD"
            detail = f" ({check.detail})" if check.detail else ""
            lines.append(f"      {mark} {check.name}{detail}")
        if result.error:
            lines.append(f"      error: {result.error}")
        metrics = result.metrics
        if metrics:
            parts = []
            if "latency_seconds" in metrics:
                parts.append(f"latency {metrics['latency_seconds']:.2f}s")
            if "output_tokens_est" in metrics:
                parts.append(f"~{metrics['output_tokens_est']} output tokens")
            if "output_chars" in metrics:
                parts.append(f"{metrics['output_chars']} chars")
            if "strategy" in metrics:
                parts.append(f"strategy {metrics['strategy']}")
            if "chunks" in metrics:
                parts.append(f"{metrics['chunks']} chunks")
            if parts:
                lines.append(f"      metrics: {', '.join(parts)}")
        lines.append("")
    lines += [
        f"{report.passed_count}/{len(report.results)} cases passed "
        f"in {report.duration_seconds:.2f}s",
        "",
        DISCLAIMER,
    ]
    return "\n".join(lines)
