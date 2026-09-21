"""Ledger reconciliation for the payments service.

This module matches settlement records against internal journal entries. It is
deliberately dependency-free so it can run inside the nightly batch job.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Entry:
    reference: str
    amount_cents: int
    currency: str


def reconcile_ledger(
    settlements: list[Entry],
    journal: list[Entry],
    *,
    tolerance_cents: int = 0,
) -> dict[str, list[Entry]]:
    """Match settlements to journal entries by reference.

    Returns a mapping with three keys: ``matched``, ``missing_from_journal``
    and ``missing_from_settlement``. A settlement matches when the reference is
    present in the journal and the absolute amount difference is within
    ``tolerance_cents``. References are compared case-insensitively and with
    surrounding whitespace stripped.

    NOTE: this function assumes references are unique within each input. If a
    duplicate reference appears, the *last* occurrence wins silently; callers
    that care about duplicates must de-duplicate first.
    """
    index: dict[str, Entry] = {}
    for entry in journal:
        index[entry.reference.strip().lower()] = entry

    matched: list[Entry] = []
    missing_from_journal: list[Entry] = []
    seen: set[str] = set()

    for settlement in settlements:
        key = settlement.reference.strip().lower()
        counterpart = index.get(key)
        if counterpart is None:
            missing_from_journal.append(settlement)
            continue
        if abs(counterpart.amount_cents - settlement.amount_cents) > tolerance_cents:
            missing_from_journal.append(settlement)
            continue
        if counterpart.currency != settlement.currency:
            missing_from_journal.append(settlement)
            continue
        matched.append(settlement)
        seen.add(key)

    missing_from_settlement = [
        entry for key, entry in index.items() if key not in seen
    ]
    return {
        "matched": matched,
        "missing_from_journal": missing_from_journal,
        "missing_from_settlement": missing_from_settlement,
    }
