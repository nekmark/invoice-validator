"""Validation for contract terms entered in the Contracts tab, kept out of
app.py so it can be unit-tested."""

from __future__ import annotations

import re

MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def normalize_month(raw: str) -> str | None:
    """Accepts 'YYYY-MM' plus forgiving variants ('2026-9', '2026/09',
    '2026, 09', '2026 09', '2026.09') and returns 'YYYY-MM', or None."""
    raw = (raw or "").strip()
    m = re.fullmatch(r"(\d{4})\s*[-/.,\s]\s*(\d{1,2})", raw)
    if not m:
        return None
    month = int(m.group(2))
    if not 1 <= month <= 12:
        return None
    return f"{m.group(1)}-{month:02d}"


def check_new_term(amount, eff_from: str, eff_to: str, existing_terms) -> tuple[str | None, str | None, str | None]:
    """Returns (error, from_month, to_month). error is None when the term can
    be saved. existing_terms: rows with effective_from / effective_to."""
    if amount is None or amount <= 0:
        return "Enter the monthly amount in EUR (greater than 0).", None, None
    start = normalize_month(eff_from)
    if not start:
        return "'Effective from' must be a month like 2026-09.", None, None
    end = None
    if (eff_to or "").strip():
        end = normalize_month(eff_to)
        if not end:
            return "'Effective to' must be a month like 2026-12, or left blank for open-ended.", None, None
        if end < start:
            return "'Effective to' is before 'Effective from'.", None, None
    for t in existing_terms:
        t_from, t_to = t["effective_from"], t["effective_to"]
        overlaps = (end is None or end >= t_from) and (t_to is None or start <= t_to)
        if overlaps:
            shown = f"{t_from} to {t_to or 'open-ended'}"
            return (
                f"This overlaps the existing term {shown}. Give the existing term an end month "
                "(delete and re-add it) or change these dates.",
                None,
                None,
            )
    return None, start, end
