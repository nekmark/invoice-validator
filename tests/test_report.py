"""Report card calculations (pure functions, made-up names only)."""

from tests._guard import refuse_real_database  # noqa: F401  (stops tests touching the real DB)
from pipeline import report


def _row(id_, name, month, status, first_seen_at, processed_at=None):
    return {"id": id_, "contractor_name": name, "service_month": month, "status": status,
            "first_seen_at": first_seen_at, "processed_at": processed_at or first_seen_at}


ROWS = [
    # Iva: September right first time; October took three versions, last one right.
    _row(1, "Olivia Carter", "2026-09", "Pass", "2026-10-01T10:00:00"),
    _row(2, "Olivia Carter", "2026-10", "Warning", "2026-11-02T09:00:00"),
    _row(3, "Olivia Carter", "2026-10", "Warning", "2026-11-03T09:00:00"),
    _row(4, "Olivia Carter", "2026-10", "Pass", "2026-11-04T09:00:00"),
    # Adam: October wrong, a second version still wrong.
    _row(5, "Adam Walker", "2026-10", "Manual Review", "2026-11-02T09:00:00"),
    _row(6, "Adam Walker", "2026-10", "Warning", "2026-11-05T09:00:00"),
    # Zoe: one right invoice; plus a file nobody could be identified for.
    _row(7, "Zoe Abbott", "2026-10", "Pass", "2026-11-02T09:00:00"),
    _row(8, None, None, "Manual Review", "2026-11-02T09:00:00"),
]


def test_versions_are_ordered_oldest_first_and_unrecognised_files_skipped():
    groups = report.versions_by_invoice(ROWS)
    assert set(groups) == {("Olivia Carter", "2026-09"), ("Olivia Carter", "2026-10"), ("Adam Walker", "2026-10"),
                           ("Zoe Abbott", "2026-10")}
    assert [v["id"] for v in groups[("Olivia Carter", "2026-10")]] == [2, 3, 4]


def test_same_second_versions_fall_back_to_id_order():
    rows = [_row(2, "Olivia Carter", "2026-10", "Pass", "2026-11-02T09:00:00"),
            _row(1, "Olivia Carter", "2026-10", "Warning", "2026-11-02T09:00:00")]
    inv = report.invoice_report(rows)[0]
    assert (inv["first_status"], inv["final_status"], inv["right"]) == ("Warning", "Pass", True)


def test_old_version_is_the_file_that_arrived_first_even_if_revalidated_later():
    rows = [_row(1, "Olivia Carter", "2026-10", "Warning", "2026-11-02T09:00:00", processed_at="2026-12-01T09:00:00"),
            _row(2, "Olivia Carter", "2026-10", "Pass", "2026-11-05T09:00:00", processed_at="2026-11-05T09:00:00")]
    inv = report.invoice_report(rows)[0]
    assert (inv["first_status"], inv["final_status"], inv["right"]) == ("Warning", "Pass", True)


def test_db_keeps_first_seen_at_across_revalidation():
    from pipeline import db
    cid = db.upsert_contractor("Olivia Carter")
    base = {"file_sha256": "v1", "original_filename": "f.pdf", "contractor_id": cid, "service_month": "2026-10"}
    iid = db.upsert_invoice({**base, "processed_at": "2026-11-02T09:00:00"})
    db.upsert_invoice({**base, "processed_at": "2026-12-01T09:00:00", "first_seen_at": "tampered"})
    db.upsert_invoice({**base, "file_sha256": "v2", "processed_at": "2026-11-05T09:00:00"})
    rows = db.list_dashboard_rows()
    seen = {r["file_sha256"]: (r["first_seen_at"], r["processed_at"]) for r in rows}
    assert seen == {"v1": ("2026-11-02T09:00:00", "2026-12-01T09:00:00"), "v2": ("2026-11-05T09:00:00", "2026-11-05T09:00:00")}
    assert [v["id"] for v in report.versions_by_invoice(rows)[("Olivia Carter", "2026-10")]][0] == iid


def test_invoice_report():
    inv = {(e["contractor"], e["month"]): e for e in report.invoice_report(ROWS)}
    assert [(e["contractor"], e["month"]) for e in report.invoice_report(ROWS)] == [
        ("Zoe Abbott", "2026-10"), ("Olivia Carter", "2026-09"), ("Olivia Carter", "2026-10"), ("Adam Walker", "2026-10")]
    iva_oct = inv[("Olivia Carter", "2026-10")]
    assert iva_oct["attempts"] == 3 and iva_oct["new_versions"] == 2
    assert iva_oct["first_status"] == "Warning" and iva_oct["final_status"] == "Pass"
    assert iva_oct["right"] and not iva_oct["right_first_time"]
    assert inv[("Olivia Carter", "2026-09")]["right_first_time"]
    adam = inv[("Adam Walker", "2026-10")]
    assert not adam["right"] and adam["attempts"] == 2 and adam["final_status"] == "Warning"


def test_contractor_report_and_totals():
    invoices = report.invoice_report(ROWS)
    per = {c["contractor"]: c for c in report.contractor_report(invoices)}
    assert list(per) == ["Zoe Abbott", "Olivia Carter", "Adam Walker"]  # surname order
    iva = per["Olivia Carter"]
    assert (iva["invoices"], iva["right"], iva["right_first_time"]) == (2, 2, 1)
    assert (iva["attempts"], iva["new_versions"], iva["most_attempts"], iva["success_pct"]) == (4, 2, 3, 100)
    assert per["Adam Walker"]["success_pct"] == 0 and per["Zoe Abbott"]["success_pct"] == 100
    assert report.totals(invoices) == {
        "contractors": 3, "invoices": 4, "right": 3, "right_first_time": 2, "attempts": 7, "new_versions": 3}


def test_invoice_without_validation_counts_as_open():
    rows = [_row(1, "Olivia Carter", "2026-10", None, "2026-11-02T09:00:00")]
    inv = report.invoice_report(rows)[0]
    assert inv["final_status"] == report.OPEN and not inv["right"]
    assert report.totals([]) == {"contractors": 0, "invoices": 0, "right": 0, "right_first_time": 0,
                                 "attempts": 0, "new_versions": 0}
