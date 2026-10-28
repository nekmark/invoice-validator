"""Generates synthetic (fake) invoice PDFs for tests. Never use real
contractor data here — names/amounts below are entirely made up."""

from pathlib import Path

from reportlab.pdfgen import canvas

FIXTURES_DIR = Path(__file__).resolve().parent


def _write_pdf(filename: str, lines: list[str]):
    path = FIXTURES_DIR / filename
    c = canvas.Canvas(str(path), invariant=1)
    y = 800
    for line in lines:
        c.drawString(50, y, line)
        y -= 20
    c.save()
    return path


def make_all():
    # Clean match: amount, issue date, and USD conversion all correct.
    _write_pdf(
        "Test Contractor_2026-01.pdf",
        [
            "Invoice Number: INV-2026-001",
            "Invoice Date of Issue: 2026-01-31",
            "Service Period: January 2026",
            "Currency: EUR",
            "Monthly Compensation Total EUR: 4,000.00",
            "Total Due USD: 4,340.00",
        ],
    )

    # Amount mismatch.
    _write_pdf(
        "Test Contractor_2026-02.pdf",
        [
            "Invoice Number: INV-2026-002",
            "Invoice Date of Issue: 2026-02-28",
            "Service Period: February 2026",
            "Currency: EUR",
            "Monthly Compensation Total EUR: 3,800.00",
            "Total Due USD: 4,123.00",
        ],
    )

    # Wrong issue date (not last day of month).
    _write_pdf(
        "Test Contractor_2026-03.pdf",
        [
            "Invoice Number: INV-2026-003",
            "Invoice Date of Issue: 2026-03-15",
            "Service Period: March 2026",
            "Currency: EUR",
            "Monthly Compensation Total EUR: 4,000.00",
            "Total Due USD: 4,340.00",
        ],
    )

    # On-call present but separable — should validate standard amount only.
    _write_pdf(
        "Test Contractor_2026-04.pdf",
        [
            "Invoice Number: INV-2026-004",
            "Invoice Date of Issue: 2026-04-30",
            "Service Period: April 2026",
            "Currency: EUR",
            "Monthly Compensation Total EUR: 4,000.00",
            "On Call Fee EUR: 400.00",
            "Total Due USD: 4,774.00",
        ],
    )

    # On-call combined with total, not separable -> Manual Review.
    _write_pdf(
        "Test Contractor_2026-05.pdf",
        [
            "Invoice Number: INV-2026-005",
            "Invoice Date of Issue: 2026-05-31",
            "Service Period: May 2026",
            "Currency: EUR",
            "Grand Total (incl. On Call) EUR: 4,400.00",
        ],
    )

    # Missing issue date entirely.
    _write_pdf(
        "Test Contractor_2026-06.pdf",
        [
            "Invoice Number: INV-2026-006",
            "Service Period: June 2026",
            "Currency: EUR",
            "Monthly Compensation Total EUR: 4,000.00",
            "Total Due USD: 4,340.00",
        ],
    )

    # Filename doesn't match naming convention.
    _write_pdf(
        "not_a_valid_filename.pdf",
        ["Invoice Number: INV-9999", "Monthly Compensation Total EUR: 4,000.00"],
    )


if __name__ == "__main__":
    make_all()
    print("Fixtures written to", FIXTURES_DIR)
