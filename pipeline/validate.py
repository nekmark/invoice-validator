"""Rule A (contracted EUR amount) / Rule B (issue date) / Rule C (ECB USD
conversion) / Rule D (on-call extra) validation logic. Pure local
computation — no network, no I/O beyond what db.py and fx.py already do.

Rule D: when on-call hours were imported for the contractor and month
(pipeline/oncall.py), the expected amount is the contract plus the computed
on-call extra. The invoice may show the extra as its own line (any label),
fold it into the total, or omit it — the comparison is always on the
total, trying each reading of the invoice's figures.

Status priority when multiple issues apply: Manual Review > Unable to
Verify > Warning > Pass — a data-quality problem (can't read/trust a field)
is treated as more blocking than an external rate-fetch failure, which in
turn is more blocking than a confirmed mismatch.
"""

import datetime

from pipeline import db, fx, oncall

AMOUNT_TOLERANCE = 0.01  # one cent
# Business rule: an UNDERPAYMENT of up to this many EUR — the invoice is
# lower than the contract, on the EUR amount or on the USD amount converted
# at the ECB rate — is not treated as a mistake (contractor rounding). The
# invoice still passes, with a note showing the gap. Any OVERPAYMENT beyond
# cent rounding is a Warning. Adjust to taste.
ACCEPTABLE_DIFFERENCE_EUR = 5.0


def _differs(a: float, b: float, tolerance: float) -> bool:
    # Round the gap first: in floating point 4000.01 - 4000.00 is
    # 0.01000000000002, which would otherwise count as more than one cent.
    return round(abs(a - b), 4) > tolerance + 1e-9


def _eur_tolerance(fields: dict, fx_result) -> float:
    """A EUR amount printed as the equivalent of a USD total is USD (rounded
    to whole cents) / rate, so it can legitimately differ from the contract
    by up to one US cent's worth of EUR at that rate. Amounts invoiced
    directly in EUR must match to the cent."""
    if fields.get("standard_amount_source") == "usd_equivalent" and fx_result:
        return round(0.01 / fx_result["rate"], 4) + AMOUNT_TOLERANCE
    return AMOUNT_TOLERANCE


def _compare(candidates, expected, tolerance, unit, rate=None):
    """candidates: [(description, value)] readings of the invoice. Returns
    (matched_value, note_or_warning, is_warning). A reading within tolerance
    passes; one within the accepted underpayment passes with a note;
    otherwise the closest reading is reported as a Warning."""
    best = None
    for desc, value in candidates:
        gap = round(value - expected, 2)
        gap_eur = round(gap / rate, 2) if rate else gap
        if not _differs(value, expected, tolerance):
            return value, None, False
        if best is None or abs(gap) < abs(best[2]):
            best = (desc, value, gap, gap_eur)
    for desc, value in candidates:
        gap = round(value - expected, 2)
        gap_eur = round(gap / rate, 2) if rate else gap
        if -ACCEPTABLE_DIFFERENCE_EUR <= gap_eur < 0:
            note = (f"Note: {unit} amount is {gap:+,.2f} {unit}"
                    + (f" ({gap_eur:+,.2f} EUR at the ECB rate)" if rate else "")
                    + f" below the expected amount, within the accepted {ACCEPTABLE_DIFFERENCE_EUR:,.0f} EUR underpayment.")
            return value, note, False
    desc, value, gap, gap_eur = best
    return value, (desc, value, gap), True


def validate_invoice(fields: dict, contractor_id: int) -> dict:
    warnings = []
    status_flags = []

    service_month = fields["service_month"]
    if fields["service_month_source"] == "conflict":
        warnings.append("Service month from the filename conflicts with the period stated in the invoice text.")
        status_flags.append("manual_review")
    if not service_month:
        warnings.append("Could not determine the invoiced service month.")
        status_flags.append("manual_review")

    expected_eur = None
    adjustment = 0.0
    if service_month:
        terms = db.get_effective_contract_terms(contractor_id, service_month)
        if len(terms) == 0:
            warnings.append(f"No contract term configured for this contractor covering {service_month}.")
            status_flags.append("manual_review")
        elif len(terms) > 1:
            warnings.append(f"Multiple overlapping contract terms found for {service_month} — ambiguous.")
            status_flags.append("manual_review")
        else:
            # Contracted amount plus any standing adjustment configured on the
            # term (a known, accepted difference the invoice always shows).
            adjustment = terms[0]["adjustment_eur"] or 0.0
            expected_eur = round(terms[0]["amount_eur"] + adjustment, 2)

    # Rule D: on-call extra from the imported attendance sheet.
    oncall_row = db.get_oncall_hours(contractor_id, service_month) if service_month else None
    oncall_extra = None
    if oncall_row is not None and expected_eur is not None:
        calc = oncall.compute_extra(oncall_row, terms[0]["amount_eur"])
        oncall_extra = calc["total_eur"]
        if oncall_row["unreadable"]:
            warnings.append(
                "The on-call sheet has an entry that couldn't be read for this month, so the on-call extra "
                "may be incomplete: " + "; ".join(oncall_row["notes"][:3])
            )
            status_flags.append("manual_review")
        warnings.append(
            f"Note: on-call extra of {oncall_extra:,.2f} EUR expected ({oncall.hours_summary(oncall_row)} at "
            f"{calc['hourly_eur']:,.2f} EUR/hour = contract / {oncall.WORKING_DAYS_PER_MONTH} days / "
            f"{oncall.HOURS_PER_DAY} hours)."
        )
        expected_eur = round(expected_eur + oncall_extra, 2)
    elif oncall_row is not None and oncall_row["notes"] and expected_eur is None:
        pass  # no contract term: already Manual Review above
    elif fields.get("oncall_present"):
        warnings.append(
            "The invoice mentions On Call but no on-call hours have been imported for this month "
            "(On-call tab), so the extra can't be checked."
        )
        status_flags.append("manual_review")
    expected_label = "the contracted amount" + (f" plus the on-call extra of {oncall_extra:,.2f} EUR" if oncall_extra else "")

    # Rule C: EUR -> USD conversion at the ECB reference rate. Only applies
    # when the invoice actually states a USD amount — EUR-only invoices skip
    # it entirely (no ECB call, no warning).
    usd_conf = fields["usd_amount_confidence"]
    expected_usd = None
    fx_result = None
    if service_month and usd_conf != "missing":
        year, month = (int(x) for x in service_month.split("-"))
        try:
            fx_result = fx.get_rate_for_month(year, month)
        except fx.FxUnavailable as e:
            warnings.append(f"Could not verify the ECB reference exchange rate: {e}")
            status_flags.append("unable_to_verify")

    # Rule A: the EUR total — contract (+ standing adjustment) (+ on-call extra).
    amt_conf = fields["standard_amount_confidence"]
    oncall_amt = fields.get("oncall_amount_value")
    oncall_cur = fields.get("oncall_amount_currency") or fields.get("currency")
    invoiced_eur = None
    eur_candidates = []
    if amt_conf == "missing" and usd_conf in ("high", "medium") and expected_eur is not None:
        # USD-only invoice: no EUR figure to compare, but Rule C below checks
        # the USD amount against contract EUR / ECB rate, which covers it.
        pass
    elif amt_conf == "missing":
        warnings.append("Standard compensation amount not found on the invoice.")
        status_flags.append("manual_review")
    elif amt_conf == "ambiguous":
        combined = fields.get("combined_eur_value")
        if combined is not None and oncall_row is not None:
            eur_candidates.append(("the total including On Call", combined))
        else:
            warnings.append("Cannot separate standard compensation from the On Call amount.")
            status_flags.append("manual_review")
    else:
        std = fields["standard_amount_value"]
        eur_candidates.append(("the standard compensation", std))
        if oncall_amt is not None and oncall_cur != "USD":
            eur_candidates.append(("the standard compensation plus the On Call line", round(std + oncall_amt, 2)))
    if eur_candidates and expected_eur is not None:
        invoiced_eur, outcome, is_warning = _compare(
            eur_candidates, expected_eur, _eur_tolerance(fields, fx_result), "EUR"
        )
        if is_warning:
            desc, value, gap = outcome
            warnings.append(
                f"Invoiced EUR amount {value:,.2f} ({desc}) does not match {expected_label}: "
                f"expected {expected_eur:,.2f} EUR"
                + (f" (including the standing adjustment of {adjustment:+,.2f} EUR)" if adjustment else "")
                + f", difference {gap:+,.2f} EUR."
            )
            status_flags.append("warning")
        elif outcome:
            warnings.append(outcome)

    # Rule B: the date of taxable supply, when the invoice states one,
    # must be the last calendar day of the invoiced month. Invoices without
    # that date fall back to checking the issue date instead.
    expected_last_day = None
    if service_month:
        year, month = (int(x) for x in service_month.split("-"))
        expected_last_day = fx.last_day_of_month(year, month)

    supply_conf = fields.get("taxable_supply_confidence") or "missing"
    if supply_conf == "ambiguous":
        warnings.append("Multiple conflicting dates of taxable supply found on the invoice.")
        status_flags.append("manual_review")
    elif supply_conf == "high":
        found_date = datetime.date.fromisoformat(fields["taxable_supply_date"])
        if expected_last_day is not None and found_date != expected_last_day:
            warnings.append(
                f"Date of taxable supply {found_date.isoformat()} is not the last day of the invoiced month "
                f"(expected {expected_last_day.isoformat()})."
            )
            status_flags.append("warning")
    else:
        issue_conf = fields["issue_date_confidence"]
        if issue_conf == "missing":
            warnings.append("Neither a date of taxable supply nor an issue date was found on the invoice.")
            status_flags.append("manual_review")
        elif issue_conf == "ambiguous":
            warnings.append("Multiple conflicting issue dates found on the invoice.")
            status_flags.append("manual_review")
        elif expected_last_day is not None:
            found_date = datetime.date.fromisoformat(fields["issue_date"])
            if found_date != expected_last_day:
                warnings.append(
                    f"Issue date {found_date.isoformat()} is not the last day of the invoiced month "
                    f"(expected {expected_last_day.isoformat()}; no date of taxable supply on the invoice)."
                )
                status_flags.append("warning")

    if fx_result and expected_eur is not None:
        expected_usd = round(expected_eur * fx_result["rate"], 2)

    usd_candidates = []
    if usd_conf == "ambiguous":
        combined_usd = fields.get("combined_usd_value")
        if combined_usd is not None and oncall_row is not None and expected_usd is not None:
            usd_candidates.append(("the USD total including On Call", combined_usd))
        else:
            warnings.append("Cannot determine a clear USD total separate from the On Call amount.")
            status_flags.append("manual_review")
    elif expected_usd is not None:
        usd = fields["usd_amount_value"]
        usd_candidates.append(("the USD total", usd))
        if oncall_amt is not None and oncall_cur == "USD":
            usd_candidates.append(("the USD total plus the On Call line", round(usd + oncall_amt, 2)))
    if usd_candidates and expected_usd is not None:
        _, outcome, is_warning = _compare(usd_candidates, expected_usd, AMOUNT_TOLERANCE, "USD", rate=fx_result["rate"])
        if is_warning:
            desc, value, gap = outcome
            warnings.append(
                f"Invoiced USD amount {value:,.2f} ({desc}) does not match the expected "
                f"{expected_usd:,.2f} (computed as {expected_eur:,.2f} EUR = {expected_label} x "
                f"{fx_result['rate']:.4f} USD per EUR, ECB reference rate of {fx_result['effective_date']})."
            )
            status_flags.append("warning")
        elif outcome:
            warnings.append(outcome)

    if "manual_review" in status_flags:
        status = "Manual Review"
    elif "unable_to_verify" in status_flags:
        status = "Unable to Verify"
    elif "warning" in status_flags:
        status = "Warning"
    else:
        status = "Pass"

    return {
        "status": status,
        "warnings": warnings,
        "expected_eur": expected_eur,
        "expected_usd": expected_usd,
        "invoiced_usd": fields["usd_amount_value"],
        "invoiced_eur": invoiced_eur,
        "oncall_extra_eur": oncall_extra,
        "fx_rate": fx_result["rate"] if fx_result else None,
        "fx_requested_date": fx_result["requested_date"] if fx_result else None,
        "fx_effective_date": fx_result["effective_date"] if fx_result else None,
        "fx_source": fx_result["source_url"] if fx_result else None,
        "computed_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
