"""Report card: who sent a right invoice, in how many attempts, and how many
corrected versions had to be uploaded.

Pure functions over already-loaded dashboard rows (db.list_dashboard_rows)
— no I/O. One "invoice" is one contractor + one service month. Every
distinct file validated for it is one *attempt* (db stores one row per file
content; re-validating the same file updates in place and is not an
attempt). Versions are ordered by the time each file was first validated
(first_seen_at), so the old version is always the one that was uploaded
first, whatever was re-validated later. *New versions* are the attempts
after the first one, i.e. the corrected files that were uploaded. An
invoice is *right* when its latest version passed."""

from __future__ import annotations

from pipeline.naming import surname_sort_key

RIGHT = "Pass"
OPEN = "no result yet"


def _version_key(row: dict):
    return (row.get("first_seen_at") or row.get("processed_at") or "", row.get("id") or 0)


def versions_by_invoice(rows: list[dict]) -> dict[tuple[str, str], list[dict]]:
    """{(contractor, month): [versions, oldest first]}. Rows without a
    contractor or a month (unrecognised files) are not an invoice of anyone."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        if r.get("contractor_name") and r.get("service_month"):
            groups.setdefault((r["contractor_name"], r["service_month"]), []).append(r)
    return {k: sorted(v, key=_version_key) for k, v in groups.items()}


def invoice_report(rows: list[dict]) -> list[dict]:
    """One entry per invoice (contractor + month), contractors in surname
    order, months ascending."""
    out = []
    for (contractor, month), versions in versions_by_invoice(rows).items():
        first, last = versions[0].get("status") or OPEN, versions[-1].get("status") or OPEN
        out.append({
            "contractor": contractor,
            "month": month,
            "attempts": len(versions),
            "new_versions": len(versions) - 1,
            "first_status": first,
            "final_status": last,
            "right": last == RIGHT,
            "right_first_time": first == RIGHT,
        })
    return sorted(out, key=lambda e: (surname_sort_key(e["contractor"]), e["month"]))


def contractor_report(invoices: list[dict]) -> list[dict]:
    """Per contractor, in surname order: how many of their invoices are
    right, how many were right first time, attempts and new versions."""
    per: dict[str, dict] = {}
    for inv in invoices:
        c = per.setdefault(inv["contractor"], {
            "contractor": inv["contractor"], "invoices": 0, "right": 0, "right_first_time": 0,
            "attempts": 0, "new_versions": 0, "most_attempts": 0,
        })
        c["invoices"] += 1
        c["right"] += inv["right"]
        c["right_first_time"] += inv["right_first_time"]
        c["attempts"] += inv["attempts"]
        c["new_versions"] += inv["new_versions"]
        c["most_attempts"] = max(c["most_attempts"], inv["attempts"])
    for c in per.values():
        c["success_pct"] = round(100 * c["right"] / c["invoices"]) if c["invoices"] else 0
    return sorted(per.values(), key=lambda c: surname_sort_key(c["contractor"]))


def totals(invoices: list[dict]) -> dict:
    return {
        "contractors": len({inv["contractor"] for inv in invoices}),
        "invoices": len(invoices),
        "right": sum(inv["right"] for inv in invoices),
        "right_first_time": sum(inv["right_first_time"] for inv in invoices),
        "attempts": sum(inv["attempts"] for inv in invoices),
        "new_versions": sum(inv["new_versions"] for inv in invoices),
    }
