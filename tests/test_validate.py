from tests._guard import refuse_real_database  # noqa: F401  (stops tests touching the real DB)
import pytest

from pipeline import db, fx, validate

FAKE_FX = {
    "rate": 1.085,
    "effective_date": "2026-01-31",
    "requested_date": "2026-01-31",
    "source_url": "https://example.invalid/ecb",
    "retrieved_at": "2026-01-31T00:00:00",
    "from_cache": False,
}


def make_fields(**overrides):
    base = {
        "service_month": "2026-01",
        "service_month_source": "filename",
        "issue_date": "2026-01-31",
        "issue_date_confidence": "high",
        "standard_amount_value": 4000.0,
        "standard_amount_confidence": "high",
        "usd_amount_value": round(4000.0 * 1.085, 2),
        "usd_amount_confidence": "high",
    }
    base.update(overrides)
    return base


@pytest.fixture
def contractor_id():
    cid = db.upsert_contractor("Synthetic Contractor")
    db.add_contract_term(cid, 4000.0, "2026-01", None)
    return cid


def test_pass(monkeypatch, contractor_id):
    monkeypatch.setattr(fx, "get_rate_for_month", lambda y, m: FAKE_FX)
    result = validate.validate_invoice(make_fields(), contractor_id)
    assert result["status"] == "Pass"
    assert result["warnings"] == []


def test_eur_amount_mismatch(monkeypatch, contractor_id):
    monkeypatch.setattr(fx, "get_rate_for_month", lambda y, m: FAKE_FX)
    result = validate.validate_invoice(make_fields(standard_amount_value=3800.0), contractor_id)
    assert result["status"] == "Warning"
    assert any("does not match the contracted" in w for w in result["warnings"])


def test_issue_date_not_last_day(monkeypatch, contractor_id):
    monkeypatch.setattr(fx, "get_rate_for_month", lambda y, m: FAKE_FX)
    result = validate.validate_invoice(make_fields(issue_date="2026-01-15"), contractor_id)
    assert result["status"] == "Warning"
    assert any("not the last day" in w for w in result["warnings"])


def test_usd_conversion_mismatch(monkeypatch, contractor_id):
    monkeypatch.setattr(fx, "get_rate_for_month", lambda y, m: FAKE_FX)
    result = validate.validate_invoice(make_fields(usd_amount_value=1234.56), contractor_id)
    assert result["status"] == "Warning"
    assert any("Invoiced USD amount" in w for w in result["warnings"])


def test_oncall_ambiguous_forces_manual_review(monkeypatch, contractor_id):
    monkeypatch.setattr(fx, "get_rate_for_month", lambda y, m: FAKE_FX)
    result = validate.validate_invoice(
        make_fields(standard_amount_confidence="ambiguous", standard_amount_value=None), contractor_id
    )
    assert result["status"] == "Manual Review"


def test_no_contract_term_is_manual_review(monkeypatch):
    cid = db.upsert_contractor("No Contract Contractor")
    monkeypatch.setattr(fx, "get_rate_for_month", lambda y, m: FAKE_FX)
    result = validate.validate_invoice(make_fields(), cid)
    assert result["status"] == "Manual Review"
    assert any("No contract term configured" in w for w in result["warnings"])


def test_fx_unavailable_is_unable_to_verify(monkeypatch, contractor_id):
    def raise_unavailable(y, m):
        raise fx.FxUnavailable("network down")

    monkeypatch.setattr(fx, "get_rate_for_month", raise_unavailable)
    result = validate.validate_invoice(make_fields(), contractor_id)
    assert result["status"] == "Unable to Verify"


def test_missing_issue_date_is_manual_review(monkeypatch, contractor_id):
    monkeypatch.setattr(fx, "get_rate_for_month", lambda y, m: FAKE_FX)
    result = validate.validate_invoice(
        make_fields(issue_date=None, issue_date_confidence="missing"), contractor_id
    )
    assert result["status"] == "Manual Review"


def test_eur_only_invoice_passes_without_fx_call(monkeypatch, contractor_id):
    def must_not_be_called(y, m):
        raise AssertionError("ECB must not be queried for a EUR-only invoice")

    monkeypatch.setattr(fx, "get_rate_for_month", must_not_be_called)
    result = validate.validate_invoice(
        make_fields(usd_amount_value=None, usd_amount_confidence="missing", usd_amount_snippet=None), contractor_id
    )
    assert result["status"] == "Pass", result["warnings"]
    assert result["expected_usd"] is None


def test_cent_rounding_in_eur_equivalent_is_not_a_warning(monkeypatch, contractor_id):
    # contract 4000 EUR at 1.085 -> 4340.00 USD; a EUR equivalent printed from the
    # rounded USD figure may be a cent off
    monkeypatch.setattr(fx, "get_rate_for_month", lambda y, m: FAKE_FX)
    fields = make_fields(standard_amount_value=3999.99, standard_amount_source="usd_equivalent", usd_amount_value=4340.00)
    assert validate.validate_invoice(fields, contractor_id)["status"] == "Pass"


def test_real_eur_difference_still_flagged(monkeypatch, contractor_id):
    monkeypatch.setattr(fx, "get_rate_for_month", lambda y, m: FAKE_FX)
    fields = make_fields(standard_amount_value=3990.00, standard_amount_source="usd_equivalent",
                         usd_amount_value=round(3990.00 * 1.085, 2))
    result = validate.validate_invoice(fields, contractor_id)
    assert result["status"] == "Warning"
    assert any(w.startswith("Invoiced EUR amount") for w in result["warnings"])


# --- Rule D: on-call extra ----------------------------------------------------

def _oncall(contractor_id, **hours):
    from pipeline import oncall
    db.upsert_oncall_hours(contractor_id, "2026-01", {k: hours.get(k, 0) for k in oncall.KIND_KEYS},
                           hours.get("unreadable", False), hours.get("notes", []), "sheet.xlsx", "t")


def test_oncall_extra_as_separate_line_passes(monkeypatch, contractor_id):
    monkeypatch.setattr(fx, "get_rate_for_month", lambda y, m: FAKE_FX)
    _oncall(contractor_id, standby_standard=20, active_evening=2)        # 50 + 67.50 = 117.50
    fields = make_fields(oncall_present=True, oncall_amount_value=117.50, oncall_amount_currency="EUR",
                         usd_amount_value=round(4117.50 * 1.085, 2))
    result = validate.validate_invoice(fields, contractor_id)
    assert result["status"] == "Pass", result["warnings"]
    assert result["expected_eur"] == 4117.50 and result["oncall_extra_eur"] == 117.50
    assert result["invoiced_eur"] == 4117.50
    assert any(w.startswith("Note: on-call extra of 117.50 EUR") for w in result["warnings"])


def test_oncall_extra_folded_into_the_total_passes(monkeypatch, contractor_id):
    monkeypatch.setattr(fx, "get_rate_for_month", lambda y, m: FAKE_FX)
    _oncall(contractor_id, standby_standard=20, active_evening=2)
    fields = make_fields(standard_amount_value=4117.50, usd_amount_value=round(4117.50 * 1.085, 2))
    assert validate.validate_invoice(fields, contractor_id)["status"] == "Pass"


def test_oncall_extra_in_a_combined_total_passes(monkeypatch, contractor_id):
    monkeypatch.setattr(fx, "get_rate_for_month", lambda y, m: FAKE_FX)
    _oncall(contractor_id, standby_standard=20, active_evening=2)
    fields = make_fields(standard_amount_confidence="ambiguous", standard_amount_value=None,
                         combined_eur_value=4117.50, oncall_present=True,
                         usd_amount_confidence="ambiguous", usd_amount_value=None,
                         combined_usd_value=round(4117.50 * 1.085, 2))
    result = validate.validate_invoice(fields, contractor_id)
    assert result["status"] == "Pass", result["warnings"]


def test_oncall_extra_missing_from_the_invoice_is_a_warning(monkeypatch, contractor_id):
    monkeypatch.setattr(fx, "get_rate_for_month", lambda y, m: FAKE_FX)
    _oncall(contractor_id, standby_standard=20, active_evening=2)
    result = validate.validate_invoice(make_fields(), contractor_id)  # bare contract amount, extra not billed
    assert result["status"] == "Warning"
    assert any("does not match the contracted amount plus the on-call extra of 117.50 EUR" in w
               and "difference -117.50 EUR" in w for w in result["warnings"])


def test_small_oncall_extra_left_out_is_an_accepted_underpayment(monkeypatch, contractor_id):
    monkeypatch.setattr(fx, "get_rate_for_month", lambda y, m: FAKE_FX)
    _oncall(contractor_id, standby_standard=1)  # 2.50 EUR
    result = validate.validate_invoice(make_fields(usd_amount_value=round(4002.50 * 1.085, 2)), contractor_id)
    assert result["status"] == "Pass" and any("within the accepted" in w for w in result["warnings"])


def test_oncall_in_usd_uses_the_ecb_rate(monkeypatch, contractor_id):
    monkeypatch.setattr(fx, "get_rate_for_month", lambda y, m: FAKE_FX)
    _oncall(contractor_id, standby_standard=20, active_evening=2)
    expected_usd = round(4117.50 * 1.085, 2)
    fields = make_fields(standard_amount_confidence="missing", standard_amount_value=None, currency="USD",
                         usd_amount_value=round(4000 * 1.085, 2), oncall_present=True,
                         oncall_amount_value=round(117.50 * 1.085, 2), oncall_amount_currency="USD")
    result = validate.validate_invoice(fields, contractor_id)
    assert result["status"] == "Pass", result["warnings"]
    assert result["expected_usd"] == expected_usd
    wrong = validate.validate_invoice({**fields, "oncall_amount_value": 10.0}, contractor_id)
    assert wrong["status"] == "Warning" and any("plus the on-call extra" in w for w in wrong["warnings"])


def test_oncall_mentioned_but_no_hours_imported_is_manual_review(monkeypatch, contractor_id):
    monkeypatch.setattr(fx, "get_rate_for_month", lambda y, m: FAKE_FX)
    result = validate.validate_invoice(make_fields(oncall_present=True), contractor_id)
    assert result["status"] == "Manual Review"
    assert any("no on-call hours have been imported" in w for w in result["warnings"])


def test_unreadable_oncall_entry_is_manual_review(monkeypatch, contractor_id):
    monkeypatch.setattr(fx, "get_rate_for_month", lambda y, m: FAKE_FX)
    _oncall(contractor_id, standby_standard=20, unreadable=True, notes=["2026-01-09: unreadable on-call work entry"])
    result = validate.validate_invoice(make_fields(standard_amount_value=4050.0,
                                                   usd_amount_value=round(4050.0 * 1.085, 2)), contractor_id)
    assert result["status"] == "Manual Review"
    assert any("couldn't be read" in w and "2026-01-09" in w for w in result["warnings"])


def test_no_oncall_hours_and_no_mention_is_unchanged(monkeypatch, contractor_id):
    monkeypatch.setattr(fx, "get_rate_for_month", lambda y, m: FAKE_FX)
    result = validate.validate_invoice(make_fields(), contractor_id)
    assert result["status"] == "Pass" and result["oncall_extra_eur"] is None and result["invoiced_eur"] == 4000.0


@pytest.mark.parametrize("gap, status", [
    (-0.17, "Pass"), (-5.00, "Pass"),                          # underpayment up to 5 EUR is accepted
    (-5.01, "Warning"), (-25.0, "Warning"),                    # bigger underpayment
    (0.17, "Warning"), (5.00, "Warning"), (25.0, "Warning"),   # any overpayment beyond rounding
])
def test_acceptable_eur_difference_boundary(monkeypatch, contractor_id, gap, status):
    monkeypatch.setattr(fx, "get_rate_for_month", lambda y, m: FAKE_FX)
    # USD-free EUR invoice so only the EUR check is exercised
    fields = make_fields(standard_amount_value=4000.0 + gap, standard_amount_source="invoice_line",
                         usd_amount_value=None, usd_amount_confidence="missing", usd_amount_snippet=None)
    result = validate.validate_invoice(fields, contractor_id)
    assert result["status"] == status, result["warnings"]
    if status == "Pass" and gap:
        assert any(w.startswith("Note:") for w in result["warnings"])


def test_usd_gap_within_5_eur_passes_with_note(monkeypatch, contractor_id):
    monkeypatch.setattr(fx, "get_rate_for_month", lambda y, m: FAKE_FX)
    expected_usd = round(4000.0 * 1.085, 2)
    ok = validate.validate_invoice(make_fields(usd_amount_value=expected_usd - 4.00), contractor_id)  # ~3.69 EUR
    assert ok["status"] == "Pass"
    assert any("USD amount is -4.00 USD" in w for w in ok["warnings"])
    bad = validate.validate_invoice(make_fields(usd_amount_value=expected_usd - 6.00), contractor_id)  # ~5.53 EUR
    assert bad["status"] == "Warning"
    over = validate.validate_invoice(make_fields(usd_amount_value=expected_usd + 4.00), contractor_id)  # ~3.69 EUR over
    assert over["status"] == "Warning"
    cent = validate.validate_invoice(make_fields(usd_amount_value=expected_usd + 0.01), contractor_id)  # rounding
    assert cent["status"] == "Pass"


def test_one_cent_usd_float_artifact_is_not_a_warning(monkeypatch, contractor_id):
    monkeypatch.setattr(fx, "get_rate_for_month", lambda y, m: FAKE_FX)
    fields = make_fields(usd_amount_value=round(4000.0 * 1.085, 2) + 0.01)
    assert validate.validate_invoice(fields, contractor_id)["status"] == "Pass"


def test_usd_only_invoice_checked_via_usd(monkeypatch, contractor_id):
    monkeypatch.setattr(fx, "get_rate_for_month", lambda y, m: FAKE_FX)
    ok = make_fields(standard_amount_value=None, standard_amount_confidence="missing", standard_amount_snippet=None)
    assert validate.validate_invoice(ok, contractor_id)["status"] == "Pass"
    bad = dict(ok, usd_amount_value=4000.00)
    assert validate.validate_invoice(bad, contractor_id)["status"] == "Warning"


def _supply(date):
    return dict(taxable_supply_date=date, taxable_supply_confidence="high", taxable_supply_snippet="Fecha de devengo")


def test_taxable_supply_on_last_day_passes_even_if_issued_next_month(monkeypatch, contractor_id):
    monkeypatch.setattr(fx, "get_rate_for_month", lambda y, m: FAKE_FX)
    fields = make_fields(issue_date="2026-02-03", **_supply("2026-01-31"))
    assert validate.validate_invoice(fields, contractor_id)["status"] == "Pass"


def test_taxable_supply_not_last_day_warns(monkeypatch, contractor_id):
    monkeypatch.setattr(fx, "get_rate_for_month", lambda y, m: FAKE_FX)
    result = validate.validate_invoice(make_fields(**_supply("2026-01-30")), contractor_id)
    assert result["status"] == "Warning"
    assert any("Date of taxable supply" in w for w in result["warnings"])


def test_without_taxable_supply_issue_date_still_checked(monkeypatch, contractor_id):
    monkeypatch.setattr(fx, "get_rate_for_month", lambda y, m: FAKE_FX)
    result = validate.validate_invoice(make_fields(issue_date="2026-02-03"), contractor_id)
    assert result["status"] == "Warning"
    assert any("Issue date" in w for w in result["warnings"])


def test_standing_adjustment_changes_expected_amount(monkeypatch, contractor_id):
    monkeypatch.setattr(fx, "get_rate_for_month", lambda y, m: FAKE_FX)
    term = db.list_contract_terms(contractor_id)[0]
    db.set_term_adjustment(term["id"], -98.86, "known difference")
    expected = 4000.0 - 98.86
    fields = make_fields(standard_amount_value=expected, usd_amount_value=round(expected * 1.085, 2))
    result = validate.validate_invoice(fields, contractor_id)
    assert result["status"] == "Pass", result["warnings"]
    assert result["expected_eur"] == expected
    # The unadjusted contract amount is now the one that gets flagged
    result = validate.validate_invoice(make_fields(), contractor_id)
    assert result["status"] == "Warning"
    assert any("standing adjustment" in w for w in result["warnings"])
