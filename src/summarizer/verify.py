"""Mechanically flag summary claims that do not appear in the source.

This is deliberately *not* a judge model. It extracts two cheap, checkable
claim types from a summary and greps the source:

  * **numbers** — a numeric token (with optional uncertainty) that does not
    occur in the source text is flagged. Rounded values still match because
    "1.60" is a substring of "1.6004".
  * **acronym expansions** — a ``ACRONYM (some phrase)`` where the expansion
    phrase does not occur in the source is flagged. This catches invented
    expansions like "M.E.W. (Molecular Ensemble with Water)" even though the
    acronym itself is real.

It is a smoke signal, not proof: extracted text can be broken
("silico ne oil"), and computed values (a percentage derived from a
correlation coefficient) legitimately do not appear verbatim. Flags are for
review, exactly like the evaluation harness's mechanical checks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

__all__ = ["UnverifiedClaim", "VerificationReport", "verify_summary"]

_NUMBER_RE = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?:\(\d+\))?")
_EXPANSION_RE = re.compile(r"([A-Z][A-Za-z.]{1,15})\.?\s*\(([A-Za-z][A-Za-z0-9 ,\-/]{3,60})\)")
_UNCERTAINTY_RE = re.compile(r"(\d)\(\d+\)")


def _normalize(text: str) -> str:
    text = _UNCERTAINTY_RE.sub(r"\1", text)
    return text.replace(",", "")


def _clean_phrase(phrase: str) -> str:
    return re.sub(r"\s+", " ", phrase).strip().lower()


@dataclass(frozen=True)
class UnverifiedClaim:
    kind: str  # "number" | "expansion"
    text: str

    def to_json(self) -> dict[str, str]:
        return {"kind": self.kind, "text": self.text}


@dataclass
class VerificationReport:
    numbers_checked: int = 0
    expansions_checked: int = 0
    unverified: list[UnverifiedClaim] = field(default_factory=list)
    # True when the source could not be normalized insightfully (never a
    # failure on its own); kept for callers that want to caveat output.
    limited: bool = False

    @property
    def ok(self) -> bool:
        return not self.unverified

    def _counts(self) -> str:
        numbers = f"{self.numbers_checked} number" + (
            "" if self.numbers_checked == 1 else "s"
        )
        expansions = f"{self.expansions_checked} expansion" + (
            "" if self.expansions_checked == 1 else "s"
        )
        return f"{numbers}, {expansions} checked"

    def render(self) -> str:
        if self.ok:
            return f"verification: no unverified claims ({self._counts()})"
        count = len(self.unverified)
        lines = [
            f"verification: {count} unverified "
            f"{'claim' if count == 1 else 'claims'} ({self._counts()})"
        ]
        for claim in self.unverified:
            lines.append(f"  {claim.kind}: {claim.text}")
        return "\n".join(lines)

    def to_json(self) -> dict[str, Any]:
        return {
            "numbers_checked": self.numbers_checked,
            "expansions_checked": self.expansions_checked,
            "unverified": [claim.to_json() for claim in self.unverified],
        }


def verify_summary(
    summary_text: str,
    source_text: str,
    *,
    max_items: int = 25,
) -> VerificationReport:
    """Check numbers and acronym expansions in ``summary_text`` against ``source_text``."""
    report = VerificationReport()
    if not summary_text:
        return report
    normalized_source = _normalize(source_text)
    lowered_source = _clean_phrase(source_text)

    seen_numbers: set[str] = set()
    for match in _NUMBER_RE.finditer(summary_text):
        token = match.group(0)
        normalized = _normalize(token)
        digits = normalized.replace(".", "")
        if len(digits) < 3 and "." not in normalized:
            continue  # 1, 2, 42: too common to be meaningful
        if normalized in seen_numbers:
            continue
        seen_numbers.add(normalized)
        report.numbers_checked += 1
        if normalized not in normalized_source:
            report.unverified.append(UnverifiedClaim("number", token))
            if len(report.unverified) >= max_items:
                return report

    seen_expansions: set[str] = set()
    for match in _EXPANSION_RE.finditer(summary_text):
        acronym, phrase = match.group(1), _clean_phrase(match.group(2))
        if phrase in seen_expansions:
            continue
        seen_expansions.add(phrase)
        report.expansions_checked += 1
        if phrase and phrase not in lowered_source:
            report.unverified.append(UnverifiedClaim("expansion", f"{acronym} ({match.group(2)})"))
            if len(report.unverified) >= max_items:
                break
    return report
