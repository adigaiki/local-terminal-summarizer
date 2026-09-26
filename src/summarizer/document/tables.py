"""Turn simple extracted tables into explicit label/value pairs.

A PDF text extractor flattens a table into rows of bare values with the column
labels appearing once, in a header line. A small model then has to carry the
header mapping across every row, and (measured on a real paper) it does not:
`P (GPa) a (Å) b (Å) c (Å) V (Å 3)` followed by `Pamb 9.6622(7) 13.6819(10) ...`
came back as `a = 13.6819 at 9.6622 GPa` — columns shifted by one.

This module re-renders each detected data row so every value carries its own
label ("a (Å): 13.6819(10)"), which removes the column-mapping step entirely.
It is a conservative heuristic: it only fires on a header line containing two
or more ``label (unit)`` tokens followed by at least two rows with the same
field count that are mostly numeric. Anything else passes through unchanged.
"""

from __future__ import annotations

import re

__all__ = ["structure_tables", "looks_like_header", "count_structured_tables"]

# A column label such as "P (GPa)", "a (Å)", "V (Å 3)", "2θ (°)".
_LABEL_RE = re.compile(r"[A-Za-z\u0391-\u03c9][A-Za-z0-9._\-/]*\s*\([^)]{1,14}\)")
# A numeric field, optionally with a parenthesised uncertainty: 9.6622(7), -0.75.
_NUMERIC_RE = re.compile(r"^[-+]?\d[\d.,]*(?:\(\d+\))?$|^[-+]?\.\d+(?:\(\d+\))?$")


def _labels(line: str) -> list[str]:
    return [match.group(0).strip() for match in _LABEL_RE.finditer(line)]


def _numeric_fraction(fields: list[str]) -> float:
    if not fields:
        return 0.0
    numeric = sum(1 for field in fields if _NUMERIC_RE.match(field))
    return numeric / len(fields)


def looks_like_header(line: str) -> bool:
    """True when a line has two or more ``label (unit)`` tokens."""
    return len(_labels(line)) >= 2


def _collect_rows(lines: list[str], start: int, ncols: int) -> tuple[list[list[str]], int]:
    rows: list[list[str]] = []
    index = start
    while index < len(lines):
        fields = lines[index].split()
        if len(fields) != ncols or _numeric_fraction(fields) < 0.6:
            break
        rows.append(fields)
        index += 1
    return rows, index


def structure_tables(text: str) -> tuple[str, int]:
    """Return ``(transformed_text, tables_structured)``.

    Only confidently-detected tables are rewritten; every other line is
    preserved verbatim.
    """
    lines = text.split("\n")
    out: list[str] = []
    index = 0
    tables = 0
    while index < len(lines):
        labels = _labels(lines[index])
        if len(labels) >= 2:
            rows, after = _collect_rows(lines, index + 1, len(labels))
            if len(rows) >= 2:
                out.append(lines[index])  # keep the original header line
                out.append(f"[structured table: {len(labels)} columns]")
                for row in rows:
                    pairs = " | ".join(
                        f"{label}: {value}" for label, value in zip(labels, row)
                    )
                    out.append(pairs)
                tables += 1
                index = after
                continue
        out.append(lines[index])
        index += 1
    return "\n".join(out), tables


def count_structured_tables(text: str) -> int:
    return text.count("[structured table: ")
