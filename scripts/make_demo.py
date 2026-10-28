"""Fills the app with made-up demo data so you can click around right away.

Creates (all of it fictional):
  invoices/Demo 2026-08/ and invoices/Demo 2026-09/   synthetic invoice PDFs
  oncall/demo-attendance.xlsx                          a small on-call sheet
  data/app.db                                          contractors + contract terms

Then start the app (scripts/start_app.sh), set a password, open the
📟 On-call tab and import September, and press "Validate all invoices".

The USD invoices are priced at the real ECB rate for the month when the
ECB is reachable (only a date range is sent), otherwise at a fallback rate —
offline, those two invoices simply show "Unable to Verify".

Refuses to touch an existing data/app.db unless you pass --force.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import db, fx, oncall  # noqa: E402
# The xlsx writer lives with the test fixtures; it has no test logic and
# does not touch the database.
from tests.fixtures.make_oncall_xlsx import GREEN, ORANGE, write_attendance_xlsx  # noqa: E402

FALLBACK_RATE = 1.0850  # USD per EUR, used only when the ECB can't be reached

CONTRACTORS = [
    # name, monthly EUR, payment terms (days), standing adjustment
    ("Lucía Ortega", 4000.00, 14, 0.0),
    ("Aino Virtanen", 5250.00, 30, 0.0),
    ("Kwame Mensah", 3600.00, 30, 0.0),       # invoices in USD
    ("Tomás Herrera", 6100.00, 21, -2.50),    # his invoices are always 2.50 below the contract
]
STAFF = ["Mia Novak", "Liam Okafor"]  # on the on-call sheet, not contractors


def _pdf(path: Path, lines: list[str]):
    from reportlab.pdfgen import canvas  # test-only dependency: pip install -r requirements-dev.txt

    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), invariant=1)
    y = 800
    for line in lines:
        c.drawString(50, y, line)
        y -= 20
    c.save()


def _eur_invoice(number, issued, due, month_name, amount, supply=None, extra_lines=()):
    lines = [
        f"Invoice {number}",
        "Supplier: (demo contractor)      Customer: Example Corp",
        f"Date of issue: {issued}",
        f"Due date: {due}",
    ]
    if supply:
        lines.append(f"Date of taxable supply: {supply}")
    lines += [f"Service period: {month_name}", "Currency: EUR", *extra_lines,
              f"Total due EUR: {amount:,.2f}"]
    return lines


def _usd_invoice(number, issued, due, month_name, usd, eur, rate):
    return [
        f"Invoice {number}",
        "Supplier: (demo contractor)      Customer: Example Corp",
        f"Issued on {issued}",
        f"Due on {due}",
        f"Service period: {month_name}",
        f"Software development $ {usd:,.2f}",
        f"Amounts $ {usd:,.2f}",
        f"{eur:,.2f} EUR",
        f"(Exchange rate {rate:.4f} USD / 1 EUR)",
    ]


def _rate_for(year, month) -> tuple[float, str]:
    try:
        r = fx.get_rate_for_month(year, month)
        return r["rate"], f"ECB rate of {r['effective_date']}"
    except fx.FxUnavailable as e:
        return FALLBACK_RATE, f"fallback rate (ECB not reachable: {e})"


def make_demo(force: bool = False):
    if db.DB_PATH.exists() and not force:
        sys.exit(f"{db.DB_PATH} already exists. Delete it or pass --force to replace its contents.")
    db.init_db()
    ids = {}
    for name, amount, due_days, adjustment in CONTRACTORS:
        cid = db.upsert_contractor(name)
        ids[name] = cid
        db.set_contractor_due_days(cid, due_days)
        if not db.list_contract_terms(cid):
            db.add_contract_term(cid, amount, "2026-01", None, adjustment_eur=adjustment,
                                 adjustment_note="always invoices 2.50 less" if adjustment else None)

    # Tells the pre-commit guard and the test suite that the names in this
    # database are fictional, so they may appear in code and tests.
    db.set_setting("demo_data", "1")

    inv = ROOT / "invoices"
    aug, sep = inv / "Demo 2026-08", inv / "Demo 2026-09"

    # Lucía Ortega: August exact, September 2.50 under (accepted underpayment).
    _pdf(aug / "Lucía Ortega_2026-08.pdf", _eur_invoice("2026-014", "31.08.2026", "14.09.2026", "August 2026", 4000.00))
    _pdf(sep / "Lucía Ortega_2026-09.pdf", _eur_invoice("2026-015", "30.09.2026", "14.10.2026", "September 2026", 3997.50))

    # Aino Virtanen: August issued in September but with the right date of taxable
    # supply (passes); September invoices 50 EUR too much (Warning).
    _pdf(aug / "Aino Virtanen_2026-08.pdf",
         _eur_invoice("ZL-08", "02.09.2026", "02.10.2026", "August 2026", 5250.00, supply="31.08.2026"))
    _pdf(sep / "Aino Virtanen_2026-09.pdf", _eur_invoice("ZL-09", "30.09.2026", "30.10.2026", "September 2026", 5300.00))

    # Kwame Mensah: billed in USD at the ECB rate; September is 10 USD short (Warning).
    rate_aug, note_aug = _rate_for(2026, 8)
    rate_sep, note_sep = _rate_for(2026, 9)
    usd_aug = round(3600.00 * rate_aug, 2)
    usd_sep = round(3600.00 * rate_sep, 2) - 10.00
    _pdf(aug / "Kwame Mensah_2026-08.pdf", _usd_invoice("AP-2026-08", "Aug 31, 2026", "Sep 30, 2026", "August 2026",
                                                        usd_aug, round(usd_aug / rate_aug, 2), rate_aug))
    _pdf(sep / "Kwame Mensah_2026-09.pdf", _usd_invoice("AP-2026-09", "Sep 30, 2026", "Oct 30, 2026", "September 2026",
                                                        usd_sep, round(usd_sep / rate_sep, 2), rate_sep))

    # Tomás Herrera: standing adjustment of -2.50; August issued on the 28th
    # (Warning: not the last day); September includes the on-call extra from the sheet.
    _pdf(aug / "Tomás Herrera_2026-08.pdf", _eur_invoice("NF-31", "28.08.2026", "18.09.2026", "August 2026", 6097.50))
    hours = {"standby_standard": 20, "standby_holiday": 14, "active_evening": 2, "active_night": 0, "active_holiday": 0}
    extra = oncall.compute_extra(hours, 6100.00, oncall.DEFAULT_RATES)["total_eur"]
    _pdf(sep / "Tomás Herrera_2026-09.pdf",
         _eur_invoice("NF-32", "30.09.2026", "21.10.2026", "September 2026", round(6097.50 + extra, 2),
                      extra_lines=["Monthly compensation EUR: 6,097.50", f"On Call extra EUR: {extra:,.2f}"]))

    # A scan that was never renamed: the Validate tab offers to name it.
    _pdf(inv / "scan_0931.pdf", ["Invoice 0931", "Date of issue: 30.09.2026", "Total due EUR: 1,200.00"])

    # On-call sheet for September: Tomás Herrera on call Mon-Fri 5 h standby
    # (4 days), 2 h active work on one evening, one GREEN national holiday with
    # 14 h standby; staff alongside; weekend rows orange.
    (ROOT / "oncall").mkdir(exist_ok=True)
    write_attendance_xlsx(ROOT / "oncall" / "demo-attendance.xlsx", {"Sep": {"rows": [
        (None, ["01.09.2026", "Novak Mia", "Herrera Tomás", 5, "", "", "", ""]),
        (None, ["02.09.2026", "Novak Mia", "Herrera Tomás", 5, "about 2h", "", "", ""]),
        (None, ["03.09.2026", "Okafor Liam", "Herrera Tomás", 5, "", "", "", ""]),
        (None, ["04.09.2026", "Okafor Liam", "Herrera Tomás", 5, "", "", "", ""]),
        (ORANGE, ["05.09.2026", "", "", "", "", "", "", ""]),
        (ORANGE, ["06.09.2026", "", "", "", "", "", "", ""]),
        (GREEN, ["07.09.2026", "Novak Mia", "Herrera Tomás", 14, "", "", "", ""]),
        (None, ["08.09.2026", "Novak Mia", "Okafor Liam", 5, "", "", "", ""]),
    ]}})

    print("Demo data written:")
    print(f"  {len(CONTRACTORS)} contractors with contract terms in {db.DB_PATH}")
    print(f"  9 invoice PDFs under {inv}")
    print(f"  on-call sheet {ROOT / 'oncall' / 'demo-attendance.xlsx'}")
    print(f"  USD invoices priced with: August — {note_aug}; September — {note_sep}")
    print("Now run scripts/start_app.sh, import September on the On-call tab, then validate.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="add the demo to an existing data/app.db")
    make_demo(ap.parse_args().force)
