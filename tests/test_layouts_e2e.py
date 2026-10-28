"""End-to-end tests: one synthetic PDF per real-world invoice layout, run
through the full pipeline (PDF text -> extraction -> validation -> DB).

Layouts mirror the structure of the real September 2026 invoices (label
wording, line order, number formats) with entirely made-up names, numbers
and amounts. The FX rate is mocked; no network access.

Contract: 4,000.00 EUR; mocked ECB rate 1.085 (USD per EUR) -> exactly 4,340.00 USD.
"""

from tests._guard import refuse_real_database  # noqa: F401  (stops tests touching the real DB)
import pytest
from reportlab.pdfgen import canvas

from pipeline import db, fx, process

RATE = 1.085
CONTRACT_EUR = 4000.00

FAKE_FX = {
    "rate": RATE,
    "effective_date": "2026-09-30",
    "requested_date": "2026-09-30",
    "source_url": "https://example.invalid/ecb",
    "retrieved_at": "2026-10-01T00:00:00",
    "from_cache": True,
}


def _pdf(path, lines):
    c = canvas.Canvas(str(path))
    y = 800
    for line in lines:
        c.drawString(40, y, line)
        y -= 18
    c.save()


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(fx, "get_rate_for_month", lambda y, m: FAKE_FX)

    def _run(lines, name="Ana Pereira", month="2026-09", amount=CONTRACT_EUR, adjustment=0.0):
        cid = db.upsert_contractor(name)
        if not db.list_contract_terms(cid):
            db.add_contract_term(cid, amount, "2026-09", None, adjustment_eur=adjustment)
        pdf = tmp_path / f"{name}_{month}.pdf"
        _pdf(pdf, lines)
        result = process.process_invoice_file(pdf)
        row = next(r for r in db.list_dashboard_rows() if r["id"] == result["invoice_id"])
        return result, row

    return _run


FAKTUROID_EN = [
    "Invoice 2026-0009",
    "SUPPLIER CUSTOMER",
    "Ana Pereira Example Corp",
    "Bank account 1234567890/0800 Issued on Sep 30, 2026",
    "IBAN CZ0000000000 Due on Oct 14, 2026",
    "Reference 20260009",
    "Software development $ 4,340.00",
    "Amounts $ 4,340.00T",
    "4,000.00 EUR",
    "(Exchange rate 1.0850 USD / 1 EUR)",
]


def test_fakturoid_english_layout(run):
    result, row = run(FAKTUROID_EN)
    assert result["status"] == "Pass", result["warnings"]
    assert row["invoice_number"] == "2026-0009"
    assert row["issue_date"] == "2026-09-30"
    assert row["usd_amount_value"] == 4340.00
    assert row["standard_amount_value"] == 4000.00
    assert row["due_date"] == "2026-10-14"


def test_czech_layout(run):
    result, row = run([
        "Faktura 2026009",
        "DODAVATEL ODBERATEL",
        "Bankovni ucet 1234567890/0800 Datum vystaveni 30. 09. 2026",
        "IBAN CZ0000 Datum splatnosti 14. 10. 2026",
        "Software development $ 4 340,00",
        "Celkem $ 4 340,00",
        "4 000,00 EUR",
        "(kurz 1,0850 USD / 1 EUR)",
    ])
    assert result["status"] == "Pass", result["warnings"]
    assert row["invoice_number"] == "2026009"
    assert row["usd_amount_value"] == 4340.00


def test_header_layout_usd_only(run):
    result, row = run([
        "Invoice number Issue date",
        "Invoice",
        "ABC-009 September 30, 2026",
        "Billed to Issued by",
        "4,340 USD due by October 14, 2026",
        "Consulting services 1 4,340 USD 4,340.00 USD",
        "Total excluding tax 4,340.00 USD",
        "Tax 0.00 USD",
        "Amount Due 4,340.00 USD",
    ])
    assert result["status"] == "Pass", result["warnings"]
    assert row["invoice_number"] == "ABC-009"
    assert row["issue_date"] == "2026-09-30"
    assert row["standard_amount_value"] is None  # USD-only invoice, checked via Rule C


INVOICE_DATE_LAYOUT = [
    "Invoice no. FV20260010",
    "Supplier Customer",
    "Payment method: Bank transfer",
    "Invoice date: 02.10.2026",
    "Due date: 16.10.2026",
    "Description Quantity Unit price Total",
    "Software development 1 pc 4 340,00 $ 4 340,00",
    "Total due: $ 4 340,00",
    "4 000,00 EUR",
    "(Exchange rate 1,0850 USD / 1 EUR)",
]


def test_issued_next_month_passes_on_taxable_supply_date(run):
    lines = INVOICE_DATE_LAYOUT[:5] + ["Date of taxable supply 30.09.2026"] + INVOICE_DATE_LAYOUT[5:]
    result, row = run(lines)
    assert result["status"] == "Pass", result["warnings"]
    assert row["invoice_number"] == "FV20260010"
    assert row["issue_date"] == "2026-10-02"
    assert row["taxable_supply_date"] == "2026-09-30"


def test_issued_next_month_without_taxable_supply_warns(run):
    result, _ = run(INVOICE_DATE_LAYOUT)
    assert result["status"] == "Warning"
    assert any("Issue date 2026-10-02" in w for w in result["warnings"])


def test_taxable_supply_not_last_day_warns(run):
    lines = INVOICE_DATE_LAYOUT[:5] + ["DUZP: 29.09.2026"] + INVOICE_DATE_LAYOUT[5:]
    result, _ = run(lines)
    assert result["status"] == "Warning"
    assert any("Date of taxable supply 2026-09-29" in w for w in result["warnings"])


def test_unlabelled_total_block(run):
    result, row = run([
        "Invoice 2026-09-30",
        "Bank account 1234567890/0800 Issued on Sep 30, 2026",
        "Software development and consulting 1 pc $ 4,340.00",
        "VAT 0 % $ 0.00",
        "$ 4,340.00T",
        "4,000.00 EUR",
        "(Exchange rate 1.0850 USD / 1 EUR)",
    ])
    assert result["status"] == "Pass", result["warnings"]
    assert row["usd_amount_value"] == 4340.00
    assert row["standard_amount_value"] == 4000.00


def test_whole_number_total_and_wrapped_supply_date(run):
    result, row = run([
        "Invoice Invoice no: 123456",
        "Invoice Date: 1st October 2026",
        "Date of Taxable Supply: 30th September",
        "2026",
        "Issued by: Ana Pereira",
        "Item No Item VAT rate VAT Total Price",
        "1 Software engineering 0% 0 4 340 USD",
        "Total 0 4 340 USD",
    ])
    assert result["status"] == "Pass", result["warnings"]
    assert row["invoice_number"] == "123456"
    assert row["taxable_supply_date"] == "2026-09-30"
    assert row["usd_amount_value"] == 4340.0


def test_difference_within_5_eur_passes_with_note(run):
    # 4,337.00 USD is 3 USD (~2.76 EUR) below the contract
    lines = [l.replace("4,340.00", "4,337.00").replace("4,000.00", "3,997.24") for l in FAKTUROID_EN]
    result, _ = run(lines)
    assert result["status"] == "Pass", result["warnings"]
    assert any(w.startswith("Note:") for w in result["warnings"])


def test_difference_over_5_eur_warns(run):
    # 4,330.00 USD is 10 USD (~9.22 EUR) below the contract
    lines = [l.replace("4,340.00", "4,330.00").replace("4,000.00", "3,990.78") for l in FAKTUROID_EN]
    result, _ = run(lines)
    assert result["status"] == "Warning"
    assert not any(w.startswith("Note:") for w in result["warnings"])


def test_standing_adjustment(run):
    # Contract is 98.86 EUR higher than what the invoice always shows
    result, row = run(FAKTUROID_EN, amount=CONTRACT_EUR + 98.86, adjustment=-98.86)
    assert result["status"] == "Pass", result["warnings"]
    assert row["expected_eur"] == CONTRACT_EUR


def test_no_contract_term_is_manual_review(tmp_path, monkeypatch):
    monkeypatch.setattr(fx, "get_rate_for_month", lambda y, m: FAKE_FX)
    pdf = tmp_path / "Nobody Example_2026-09.pdf"
    _pdf(pdf, FAKTUROID_EN)
    result = process.process_invoice_file(pdf)
    assert result["status"] == "Manual Review"
    assert any("No contract term" in w for w in result["warnings"])


def test_revalidation_is_stable(run, tmp_path):
    first, _ = run(FAKTUROID_EN)
    second = process.process_invoice_file(tmp_path / "Ana Pereira_2026-09.pdf")
    assert second["status"] == first["status"] == "Pass"
    assert second["invoice_id"] == first["invoice_id"]
    assert len(db.list_dashboard_rows()) == 1


def test_fx_unavailable_is_unable_to_verify(tmp_path, monkeypatch):
    def down(y, m):
        raise fx.FxUnavailable("ECB unreachable")

    monkeypatch.setattr(fx, "get_rate_for_month", down)
    cid = db.upsert_contractor("Ana Pereira")
    db.add_contract_term(cid, CONTRACT_EUR, "2026-09", None)
    pdf = tmp_path / "Ana Pereira_2026-09.pdf"
    _pdf(pdf, FAKTUROID_EN)
    assert process.process_invoice_file(pdf)["status"] == "Unable to Verify"


def test_overpayment_within_5_eur_warns(run):
    # 4,343.00 USD is 3 USD (~2.76 EUR) ABOVE the contract: only underpayments are accepted
    lines = [l.replace("4,340.00", "4,343.00").replace("4,000.00", "4,002.76") for l in FAKTUROID_EN]
    result, _ = run(lines)
    assert result["status"] == "Warning"
    assert not any(w.startswith("Note:") for w in result["warnings"])
