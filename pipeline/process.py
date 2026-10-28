"""Top-level orchestration: one PDF file in, one (invoice row + validation
row) written to the local DB. Used by both the Streamlit 'Validate' action
and the test suite."""

import datetime
from pathlib import Path

from pipeline import db, dedupe, extraction, validate


def _invoice_row(fields: dict, file_hash: str, filename: str, contractor_id):
    return {
        "file_sha256": file_hash,
        "original_filename": filename,
        "contractor_id": contractor_id,
        "invoice_number": fields["invoice_number"],
        "service_month": fields["service_month"],
        "service_month_source": fields["service_month_source"],
        "issue_date": fields["issue_date"],
        "issue_date_confidence": fields["issue_date_confidence"],
        "issue_date_snippet": fields["issue_date_snippet"],
        "due_date": fields["due_date"],
        "payment_date": fields["payment_date"],
        "taxable_supply_date": fields["taxable_supply_date"],
        "taxable_supply_confidence": fields["taxable_supply_confidence"],
        "taxable_supply_snippet": fields["taxable_supply_snippet"],
        "currency": fields["currency"],
        "standard_amount_value": fields["standard_amount_value"],
        "standard_amount_confidence": fields["standard_amount_confidence"],
        "standard_amount_snippet": fields["standard_amount_snippet"],
        "usd_amount_value": fields["usd_amount_value"],
        "usd_amount_confidence": fields["usd_amount_confidence"],
        "usd_amount_snippet": fields["usd_amount_snippet"],
        "oncall_present": int(fields["oncall_present"]),
        "oncall_snippet": fields["oncall_snippet"],
        "extraction_method": fields["extraction_method"],
        "processed_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }


def process_invoice_file(pdf_path: Path) -> dict:
    file_hash = dedupe.file_sha256(pdf_path)
    fields = extraction.extract_invoice_fields(pdf_path)

    contractor_id = db.upsert_contractor(fields["contractor_name"]) if fields["contractor_name"] else None
    dup_warning = dedupe.check_duplicate(file_hash, contractor_id, fields["invoice_number"])

    invoice_id = db.upsert_invoice(_invoice_row(fields, file_hash, pdf_path.name, contractor_id))

    if contractor_id is None:
        result = {
            "status": "Manual Review",
            "warnings": [
                "Filename doesn't match the '<Contractor>_<YYYY-MM>.pdf' naming convention "
                "— cannot identify contractor or service month."
            ],
            "expected_eur": None,
            "expected_usd": None,
            "invoiced_usd": fields["usd_amount_value"],
            "fx_rate": None,
            "fx_requested_date": None,
            "fx_effective_date": None,
            "fx_source": None,
            "computed_at": datetime.datetime.now().isoformat(timespec="seconds"),
        }
    else:
        result = validate.validate_invoice(fields, contractor_id)

    if dup_warning:
        result["warnings"].append(dup_warning)
        if result["status"] == "Pass":
            result["status"] = "Manual Review"

    db.upsert_validation(invoice_id, result)
    return {"invoice_id": invoice_id, **result}
