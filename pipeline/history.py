"""Month-by-month history per contractor, built from dashboard rows.
Pure functions over already-loaded rows — no I/O."""

from __future__ import annotations

from pipeline.naming import surname_sort_key


def difference_eur(row: dict):
    """How far the invoice is from what the contract expects, in EUR
    (negative = invoice is lower). Uses the EUR amount when the invoice has
    one, otherwise the USD gap converted at the ECB rate (USD per EUR). None if unknown."""
    # invoiced_eur is the reading of the invoice that validation compared
    # (e.g. standard + On Call line); older rows only have the standard amount.
    eur = row.get("invoiced_eur") if row.get("invoiced_eur") is not None else row.get("standard_amount_value")
    expected = row.get("expected_eur")
    if eur is not None and expected is not None:
        return round(eur - expected, 2)
    usd, exp_usd, rate = row.get("usd_amount_value"), row.get("expected_usd"), row.get("fx_rate")
    if usd is not None and exp_usd is not None and rate:
        return round((usd - exp_usd) / rate, 2)
    return None


def overview(rows: list[dict]):
    """Returns (contractors sorted by surname, months ascending,
    {(contractor, month): status})."""
    cells = {}
    for r in rows:
        if r.get("contractor_name") and r.get("service_month"):
            cells[(r["contractor_name"], r["service_month"])] = r.get("status")
    contractors = sorted({c for c, _ in cells}, key=surname_sort_key)
    months = sorted({m for _, m in cells})
    return contractors, months, cells


def contractor_history(rows: list[dict], contractor: str) -> list[dict]:
    """One entry per invoice for this contractor, oldest month first."""
    out = []
    for r in rows:
        if r.get("contractor_name") != contractor:
            continue
        out.append({
            "month": r.get("service_month"),
            "status": r.get("status"),
            "invoice_number": r.get("invoice_number"),
            "invoiced_eur": r.get("standard_amount_value"),
            "expected_eur": r.get("expected_eur"),
            "difference_eur": difference_eur(r),
            "invoiced_usd": r.get("usd_amount_value"),
            "expected_usd": r.get("expected_usd"),
            "checked_date": r.get("taxable_supply_date") or r.get("issue_date"),
        })
    return sorted(out, key=lambda h: (h["month"] or "", h["invoice_number"] or ""))


def success(cells: dict, contractor: str):
    """(passed, total, percent) over every month this contractor has an
    invoice. 'Success' = status Pass."""
    statuses = [s for (c, _), s in cells.items() if c == contractor]
    passed = sum(1 for s in statuses if s == "Pass")
    total = len(statuses)
    return passed, total, (round(100 * passed / total) if total else 0)
