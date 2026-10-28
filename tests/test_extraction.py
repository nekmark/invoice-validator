from tests._guard import refuse_real_database  # noqa: F401  (stops tests touching the real DB)
import pytest
from pathlib import Path

from pipeline import extraction
from tests.fixtures.make_fixtures import FIXTURES_DIR, make_all


def setup_module(module):
    make_all()


def test_parse_filename():
    name, year, month = extraction.parse_filename(Path("Jane Doe_2026-09.pdf"))
    assert name == "Jane Doe"
    assert year == 2026
    assert month == 9


def test_parse_filename_invalid():
    name, year, month = extraction.parse_filename(Path("not_a_valid_filename.pdf"))
    assert name is None


def test_clean_invoice_fields():
    fields = extraction.extract_invoice_fields(FIXTURES_DIR / "Test Contractor_2026-01.pdf")
    assert fields["contractor_name"] == "Test Contractor"
    assert fields["service_month"] == "2026-01"
    assert fields["issue_date"] == "2026-01-31"
    assert fields["issue_date_confidence"] == "high"
    assert fields["standard_amount_value"] == 4000.00
    assert fields["standard_amount_confidence"] == "high"
    assert fields["usd_amount_value"] == 4340.00
    assert fields["oncall_present"] is False


def test_amount_mismatch_fixture_still_extracts_cleanly():
    fields = extraction.extract_invoice_fields(FIXTURES_DIR / "Test Contractor_2026-02.pdf")
    assert fields["standard_amount_value"] == 3800.00
    assert fields["standard_amount_confidence"] == "high"


def test_wrong_issue_date_extracted_as_is():
    fields = extraction.extract_invoice_fields(FIXTURES_DIR / "Test Contractor_2026-03.pdf")
    assert fields["issue_date"] == "2026-03-15"
    assert fields["issue_date_confidence"] == "high"


def test_oncall_amount_and_combined_total_lines():
    from pipeline.extraction import find_combined_total, find_oncall_amount, EUR_CURRENCY_KEYWORDS, USD_CURRENCY_KEYWORDS
    lines = ["Monthly Compensation Total EUR: 4,000.00", "On Call Fee EUR: 400.00", "Total Due USD: 4,774.00"]
    assert find_oncall_amount(lines) == (400.0, "EUR")
    assert find_combined_total(lines, EUR_CURRENCY_KEYWORDS) is None
    combined = ["Grand Total (incl. On Call) EUR: 4,400.00", "Total incl. on-call USD: 4,774.00"]
    assert find_oncall_amount(combined) == (None, None)  # a total that mentions On Call is not an On Call line
    assert find_combined_total(combined, EUR_CURRENCY_KEYWORDS) == 4400.0
    assert find_combined_total(combined, USD_CURRENCY_KEYWORDS) == 4774.0
    assert find_oncall_amount(["Extra work / on-call: $ 130.00"]) == (130.0, "USD")
    assert find_oncall_amount(["On call: 2 hours"]) == (None, None)


def test_oncall_separable():
    fields = extraction.extract_invoice_fields(FIXTURES_DIR / "Test Contractor_2026-04.pdf")
    assert fields["oncall_present"] is True
    assert fields["standard_amount_value"] == 4000.00
    assert fields["standard_amount_confidence"] == "high"


def test_oncall_not_separable_is_ambiguous():
    fields = extraction.extract_invoice_fields(FIXTURES_DIR / "Test Contractor_2026-05.pdf")
    assert fields["oncall_present"] is True
    assert fields["standard_amount_confidence"] == "ambiguous"


def test_missing_issue_date():
    fields = extraction.extract_invoice_fields(FIXTURES_DIR / "Test Contractor_2026-06.pdf")
    assert fields["issue_date_confidence"] == "missing"
    assert fields["issue_date"] is None


# --- English / USD-billed layouts (synthetic lines mirroring real layouts) ---
import datetime as _dt

from pipeline import extraction as _ex


def _issue(lines):
    d, _, conf = _ex.find_issue_date(lines)
    return (d.isoformat() if d else None), conf


def test_issued_on_month_name():
    lines = ["Bank account 1234567890/0800 Issued on Sep 30, 2026", "IBAN ES00 Due on Oct 15, 2026"]
    assert _issue(lines) == ("2026-09-30", "high")
    assert _ex.find_date_by_keywords(lines, _ex.DUE_DATE_KEYWORDS)[0] == _dt.date(2026, 10, 15)


def test_invoice_date_variants():
    assert _issue(["Invoice date: 30.09.2026", "Due date: 14.10.2026"]) == ("2026-09-30", "high")
    assert _issue(["Invoice Date: 1st October 2026"]) == ("2026-10-01", "high")


def test_label_above_value_header():
    lines = ["Invoice number Issue date", "Invoice", "ABC-001 September 30, 2026", "Billed to Issued by"]
    assert _issue(lines) == ("2026-09-30", "high")


def test_date_of_split_across_lines():
    lines = ["Account No. Symbol Date of", "1234567890/0800 variable: 20260009 issue: 30.09.2026",
             "IBAN: ES00 1234 1234 Due 14.10.2026"]
    assert _issue(lines) == ("2026-09-30", "high")


def test_taxable_supply_date_is_not_issue_date():
    assert _issue(["Date of taxable supply Sep 30, 2026", "Fecha de devengo: 30.09.2026"]) == (None, "missing")


def test_amount_with_glued_word_and_eur_equivalent_line():
    lines = ["Item Software development $ 4,340.00", "Amounts $ 4,340.00T", "4,000.00 EUR", "(Exchange rate 1.0850 USD / 1 EUR)"]
    usd, usd_conf, usd_snip = _ex.find_amount(lines, _ex.USD_CURRENCY_KEYWORDS)
    assert (usd, usd_conf) == (4340.0, "high")
    eur, eur_conf, _ = _ex.find_eur_equivalent(lines, usd_snip)
    assert (eur, eur_conf) == (4000.0, "high")


def test_space_grouped_amounts():
    lines = ["Total due: $ 4 340,00", "4 000,00 EUR"]
    usd, _, snip = _ex.find_amount(lines, _ex.USD_CURRENCY_KEYWORDS)
    assert usd == 4340.0
    assert _ex.find_eur_equivalent(lines, snip)[0] == 4000.0
    assert _ex.find_amount(["Importe $ 4 000,00€"], _ex.USD_CURRENCY_KEYWORDS)[0] == 4000.0


def test_whole_number_total():
    assert _ex.find_amount(["Total 0 55 000 USD"], _ex.USD_CURRENCY_KEYWORDS)[0] == 55000.0


def test_no_eur_line_under_total_stays_missing():
    lines = ["Amount Due 4,000.00 USD", "Pay online"]
    _, _, snip = _ex.find_amount(lines, _ex.USD_CURRENCY_KEYWORDS)
    assert _ex.find_eur_equivalent(lines, snip)[1] == "missing"


def test_unlabelled_total_block_accepted_when_rate_agrees():
    lines = ["Consulting $ 4,340.00", "VAT 0 % $ 0.00", "$ 4,340.00T", "4,000.00 EUR", "(Exchange rate 1.0850 USD / 1 EUR)"]
    assert _ex.find_unlabelled_total_block(lines)[:2] == (4340.0, 4000.0)


def test_unlabelled_total_block_rejected_when_rate_disagrees():
    lines = ["$ 4,340.00", "3,500.00 EUR", "(Exchange rate 1.0850 USD / 1 EUR)"]
    assert _ex.find_unlabelled_total_block(lines) == (None, None, None)


@pytest.mark.parametrize("lines, expected", [
    (["Reference 12345678 Date of taxable supply Sep 30, 2026"], "2026-09-30"),
    (["Fecha de devengo: 30.09.2026"], "2026-09-30"),
    (["Date of taxable transaction 30.09.2026"], "2026-09-30"),
    (["Date of Taxable Supply: 30th September", "2026", "Issued by: X"], "2026-09-30"),
    (["Fecha de operación 30. 09. 2026"], "2026-09-30"),
    (["Invoice date: 01.10.2026"], None),
])
def test_taxable_supply_date(lines, expected):
    d, _, conf = _ex.find_taxable_supply_date(lines)
    assert (d.isoformat() if d else None) == expected
    assert conf == ("high" if expected else "missing")


def test_taxable_supply_conflict_is_ambiguous():
    assert _ex.find_taxable_supply_date(["Fecha de devengo: 30.09.2026", "Date of taxable supply Sep 29, 2026"])[2] == "ambiguous"


def test_wrapped_date_must_start_on_label_line():
    # The label line has no date fragment, so a date on the next line belongs to something else.
    assert _ex.find_taxable_supply_date(["Date of taxable supply", "Due date 14.10.2026"])[2] == "missing"


@pytest.mark.parametrize("lines, expected", [
    (["Invoice 2026-0009"], "2026-0009"),
    (["Supplier Invoice 20260009"], "20260009"),
    (["Invoice no. FV20260010"], "FV20260010"),
    (["Invoice Invoice no: 123456", "Invoice Date: 1st October 2026"], "123456"),
    (["Factura 2026009"], "2026009"),
    (["Factura Nº 2026-009"], "2026-009"),
    (["Invoice number Issue date", "Invoice", "ABC-001 September 30, 2026"], "ABC-001"),
    (["Invoice generated by Tool at site", "Invoice 2026-09-30"], "2026-09-30"),
    (["Invoice date: 30.09.2026"], None),
    (["Invoice number Issue date", "Billed to"], None),
])
def test_find_invoice_number(lines, expected):
    assert _ex.find_invoice_number(lines)[0] == expected


def test_invoice_number_with_letter_segments():
    assert _ex.find_invoice_number(["Invoice Number: INV-INT-001"])[0] == "INV-INT-001"
    assert _ex.find_invoice_number(["Invoice Number: draft"])[0] is None
