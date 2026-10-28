from tests._guard import refuse_real_database  # noqa: F401  (stops tests touching the real DB)
from reportlab.pdfgen import canvas

from pipeline import db, fx, process

FAKE_FX = {
    "rate": 1.085,
    "effective_date": "2026-01-31",
    "requested_date": "2026-01-31",
    "source_url": "https://example.invalid/ecb",
    "retrieved_at": "2026-01-31T00:00:00",
    "from_cache": False,
}


def _write_pdf(path, lines):
    c = canvas.Canvas(str(path))
    y = 800
    for line in lines:
        c.drawString(50, y, line)
        y -= 20
    c.save()


def test_full_pipeline_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(fx, "get_rate_for_month", lambda y, m: FAKE_FX)
    expected_usd = round(4000.0 * FAKE_FX["rate"], 2)

    pdf_path = tmp_path / "Integration Contractor_2026-01.pdf"
    _write_pdf(
        pdf_path,
        [
            "Invoice Number: INV-INT-001",
            "Invoice Date of Issue: 2026-01-31",
            "Service Period: January 2026",
            "Currency: EUR",
            "Monthly Compensation Total EUR: 4,000.00",
            f"Total Due USD: {expected_usd:,.2f}",
        ],
    )

    cid = db.upsert_contractor("Integration Contractor")
    db.add_contract_term(cid, 4000.0, "2026-01", None)

    result = process.process_invoice_file(pdf_path)
    assert result["status"] == "Pass", result["warnings"]


def test_duplicate_file_flagged(tmp_path, monkeypatch):
    monkeypatch.setattr(fx, "get_rate_for_month", lambda y, m: FAKE_FX)
    pdf_path = tmp_path / "Integration Contractor_2026-02.pdf"
    _write_pdf(
        pdf_path,
        [
            "Invoice Number: INV-INT-002",
            "Invoice Date of Issue: 2026-02-28",
            "Monthly Compensation Total EUR: 2,000.00",
            "Total Due USD: 2,170.00",
        ],
    )
    cid = db.upsert_contractor("Integration Contractor")
    db.add_contract_term(cid, 2000.0, "2026-02", None)

    first = process.process_invoice_file(pdf_path)
    second = process.process_invoice_file(pdf_path)
    # Re-validating the same file is a re-run, not a duplicate: same row,
    # same status, no extra warning.
    assert second["invoice_id"] == first["invoice_id"]
    assert second["status"] == first["status"]
    assert not any("already" in w for w in second["warnings"])


def test_rerun_keeps_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(fx, "get_rate_for_month", lambda y, m: FAKE_FX)
    pdf_path = tmp_path / "Integration Contractor_2026-01.pdf"
    _write_pdf(pdf_path, ["Invoice Number: INV-R-1", "Date of Issue: 2026-01-31", "Total EUR: 4,000.00"])
    cid = db.upsert_contractor("Integration Contractor")
    db.add_contract_term(cid, 4000.0, "2026-01", None)
    assert process.process_invoice_file(pdf_path)["status"] == "Pass"
    assert process.process_invoice_file(pdf_path)["status"] == "Pass"


def test_reissued_invoice_number_still_flagged(tmp_path, monkeypatch):
    monkeypatch.setattr(fx, "get_rate_for_month", lambda y, m: FAKE_FX)
    cid = db.upsert_contractor("Integration Contractor")
    db.add_contract_term(cid, 4000.0, "2026-01", None)
    a = tmp_path / "Integration Contractor_2026-01.pdf"
    _write_pdf(a, ["Invoice Number: INV-SAME-01", "Date of Issue: 2026-01-31", "Total EUR: 4,000.00"])
    process.process_invoice_file(a)
    b_dir = tmp_path / "b"; b_dir.mkdir()
    b = b_dir / "Integration Contractor_2026-01.pdf"
    _write_pdf(b, ["Invoice Number: INV-SAME-01", "Date of Issue: 2026-01-31", "Total EUR: 4,000.00", "note"])
    result = process.process_invoice_file(b)
    assert result["status"] == "Manual Review"
    assert any("possible reissue" in w for w in result["warnings"])


def test_unparseable_filename_is_manual_review(tmp_path):
    pdf_path = tmp_path / "not_a_valid_filename.pdf"
    _write_pdf(pdf_path, ["Invoice Number: INV-9999", "Monthly Compensation Total EUR: 4,000.00"])
    result = process.process_invoice_file(pdf_path)
    assert result["status"] == "Manual Review"
