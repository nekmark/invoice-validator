"""Fills the app with made-up demo data so you can click around right away.

Creates (all of it fictional):
  invoices/Demo 2026-03/ … invoices/Demo 2026-09/   synthetic invoice PDFs, seven months
  oncall/demo-attendance.xlsx                        an on-call sheet with June and September tabs
  data/app.db                                        contractors, contract terms, payment terms

Then start the app (scripts/start_app.sh), set a password, open the
📟 On-call tab and import June and September, and press "Validate all
invoices" on the Validate tab.

What the months contain (so the History and Report tabs have a story):
  Lucía Ortega    passes every month, except May (issued on the 29th) and a
                  2.50 EUR shortfall in September that is accepted with a note
  Aino Virtanen   contract rises from 5,000 to 5,250 EUR in June; she still
                  invoices 5,000 in June (Warning), invoices August in
                  September with the right date of taxable supply (passes),
                  and overcharges 50 EUR in September (Warning)
  Kwame Mensah    bills in USD at the ECB rate; July is 10 USD short (Warning)
  Tomás Herrera   has a standing -2.50 adjustment; sent a wrong April invoice
                  and then a corrected version (the Report tab counts the
                  attempt); on call in June and September; August issued on
                  the 28th (Warning)
  scan_0931.pdf   a file that was never renamed (the Validate tab offers to)

The USD invoices are priced at the real ECB rate for each month when the
ECB is reachable (only a date range is sent), otherwise at a fallback rate —
offline, those invoices simply show "Unable to Verify".

Refuses to touch an existing data/app.db unless you pass --force, and even
then only replaces a database this script made earlier (marked demo_data),
plus the "Demo YYYY-MM" folders, scan_0931.pdf and the demo sheet it wrote.
"""

from __future__ import annotations

import argparse
import calendar
import datetime as dt
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import db, fx, oncall  # noqa: E402
# The xlsx writer lives with the test fixtures; it has no test logic and
# does not touch the database.
from tests.fixtures.make_oncall_xlsx import GREEN, ORANGE, write_attendance_xlsx  # noqa: E402

FALLBACK_RATE = 1.0850  # USD per EUR, used only when the ECB can't be reached
MONTHS = [f"2026-{m:02d}" for m in range(3, 10)]  # March … September 2026

CONTRACTORS = {
    # name: (payment terms in days, [(amount EUR, from, to, standing adjustment)])
    "Lucía Ortega": (14, [(4000.00, "2026-01", None, 0.0)]),
    "Aino Virtanen": (30, [(5000.00, "2026-01", "2026-05", 0.0), (5250.00, "2026-06", None, 0.0)]),
    "Kwame Mensah": (30, [(3600.00, "2026-01", None, 0.0)]),      # invoices in USD
    "Tomás Herrera": (21, [(6100.00, "2026-01", None, -2.50)]),   # always 2.50 below the contract
}
STAFF = ["Mia Novak", "Liam Okafor", "Sofia Rossi"]  # on the on-call sheet, not contractors

# On-call hours per month for Tomás Herrera (standby normal / holiday, active evening).
ONCALL = {
    "2026-06": {"standby_standard": 25, "standby_holiday": 0, "active_evening": 3, "active_night": 1, "active_holiday": 0},
    "2026-09": {"standby_standard": 20, "standby_holiday": 14, "active_evening": 2, "active_night": 0, "active_holiday": 0},
}


def _pdf(path: Path, lines: list[str]):
    from reportlab.pdfgen import canvas  # test-only dependency: pip install -r requirements-dev.txt

    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), invariant=1)
    y = 800
    for line in lines:
        c.drawString(50, y, line)
        y -= 20
    c.save()


def _last_day(month: str) -> dt.date:
    y, m = (int(x) for x in month.split("-"))
    return dt.date(y, m, calendar.monthrange(y, m)[1])


def _dmy(d: dt.date) -> str:
    return d.strftime("%d.%m.%Y")


def _month_name(month: str) -> str:
    return _last_day(month).strftime("%B %Y")


def _eur_invoice(number, issued: dt.date, due_days: int, month: str, amount, supply: dt.date | None = None,
                 extra_lines=()):
    lines = [
        f"Invoice {number}",
        "Supplier: (demo contractor)      Customer: Example Corp",
        f"Date of issue: {_dmy(issued)}",
        f"Due date: {_dmy(issued + dt.timedelta(days=due_days))}",
    ]
    if supply:
        lines.append(f"Date of taxable supply: {_dmy(supply)}")
    lines += [f"Service period: {_month_name(month)}", "Currency: EUR", *extra_lines, f"Total due EUR: {amount:,.2f}"]
    return lines


def _usd_invoice(number, issued: dt.date, due_days: int, month: str, usd, rate):
    due = issued + dt.timedelta(days=due_days)
    return [
        f"Invoice {number}",
        "Supplier: (demo contractor)      Customer: Example Corp",
        f"Issued on {issued:%b %d, %Y}",
        f"Due on {due:%b %d, %Y}",
        f"Service period: {_month_name(month)}",
        f"Software development $ {usd:,.2f}",
        f"Amounts $ {usd:,.2f}",
        f"{round(usd / rate, 2):,.2f} EUR",
        f"(Exchange rate {rate:.4f} USD / 1 EUR)",
    ]


def _rate_for(month: str) -> tuple[float, str]:
    y, m = (int(x) for x in month.split("-"))
    try:
        r = fx.get_rate_for_month(y, m)
        return r["rate"], f"ECB rate of {r['effective_date']}"
    except fx.FxUnavailable as e:
        return FALLBACK_RATE, f"fallback rate (ECB not reachable: {e})"


def _is_demo_db(path: Path) -> bool:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = 'demo_data'").fetchone()
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()
    return bool(row and row[0] == "1")


def _weekday_rows(month: str, person: str, hours: dict) -> list:
    """One row per day of the month: the contractor and a staff member on
    weekdays with 5 h standby, weekends orange, the first weekday after the
    15th green (a national holiday) with 14 h standby when holiday standby
    is wanted; the active hours land on a few evenings."""
    rows, staff_i = [], 0
    standby_left = hours["standby_standard"]
    evening_left, night_left = hours["active_evening"], hours["active_night"]
    holiday_done = hours["standby_holiday"] == 0
    d = _last_day(month).replace(day=1)
    while d.month == int(month[-2:]):
        if d.weekday() >= 5:
            rows.append((ORANGE, [_dmy(d), "", "", "", "", "", "", ""]))
        elif not holiday_done and d.day > 15:
            rows.append((GREEN, [_dmy(d), STAFF[staff_i % len(STAFF)], person, hours["standby_holiday"], "", "", "", ""]))
            holiday_done = True
        elif standby_left > 0:
            work = ""
            if evening_left > 0:
                work, evening_left = f"about {min(evening_left, 2):g}h", evening_left - min(evening_left, 2)
            night = ""
            if night_left > 0 and not work:
                night, night_left = f"{night_left:g}h", 0
            rows.append((None, [_dmy(d), STAFF[staff_i % len(STAFF)], person, 5, work, night, "", ""]))
            standby_left -= 5
            staff_i += 1
        else:
            rows.append((None, [_dmy(d), STAFF[staff_i % len(STAFF)], STAFF[(staff_i + 1) % len(STAFF)], 5, "", "", "", ""]))
            staff_i += 1
        d += dt.timedelta(days=1)
    return rows


def _remove_earlier_output():
    """Only what this script writes: the Demo month folders, the unnamed scan
    and the demo sheet. Anything else in invoices/ or oncall/ is left alone."""
    import shutil
    for d in (ROOT / "invoices").glob("Demo 2026-*"):
        if d.is_dir():
            shutil.rmtree(d)
    (ROOT / "invoices" / "scan_0931.pdf").unlink(missing_ok=True)
    (ROOT / "oncall" / "demo-attendance.xlsx").unlink(missing_ok=True)


def make_demo(force: bool = False):
    if db.DB_PATH.exists():
        if not force:
            sys.exit(f"{db.DB_PATH} already exists. Pass --force to replace a demo database, or delete it yourself.")
        if not _is_demo_db(db.DB_PATH):
            sys.exit(f"{db.DB_PATH} was not made by this script, so it is left alone. Delete it yourself if you mean it.")
        db.DB_PATH.unlink()
        _remove_earlier_output()
    db.init_db()
    for name, (due_days, terms) in CONTRACTORS.items():
        cid = db.upsert_contractor(name)
        db.set_contractor_due_days(cid, due_days)
        for amount, start, end, adjustment in terms:
            db.add_contract_term(cid, amount, start, end, adjustment_eur=adjustment,
                                 adjustment_note="always invoices 2.50 less" if adjustment else None)
    # Tells the pre-commit guard and the test suite that the names in this
    # database are fictional, so they may appear in code and tests.
    db.set_setting("demo_data", "1")

    inv = ROOT / "invoices"
    folder = {m: inv / f"Demo {m}" for m in MONTHS}
    rate_notes = {}
    files = 0

    for i, month in enumerate(MONTHS):
        last = _last_day(month)
        n = i + 1

        # Lucía Ortega: exact every month, except May (issued on the 29th) and September (2.50 short).
        issued = last.replace(day=29) if month == "2026-05" else last
        amount = 3997.50 if month == "2026-09" else 4000.00
        _pdf(folder[month] / f"Lucía Ortega_{month}.pdf", _eur_invoice(f"2026-{n:03d}", issued, 14, month, amount))

        # Aino Virtanen: 5,000 until May, 5,250 from June — but June still says 5,000 (Warning);
        # August issued on 2 September with the right date of taxable supply; September 50 over.
        amount = 5000.00 if month <= "2026-06" else 5250.00
        if month == "2026-09":
            amount = 5300.00
        if month == "2026-08":
            lines = _eur_invoice(f"AV-{n:02d}", last + dt.timedelta(days=2), 30, month, amount, supply=last)
        else:
            lines = _eur_invoice(f"AV-{n:02d}", last, 30, month, amount)
        _pdf(folder[month] / f"Aino Virtanen_{month}.pdf", lines)

        # Kwame Mensah: USD at the ECB rate of the month's last day; July 10 USD short.
        rate, rate_notes[month] = _rate_for(month)
        usd = round(3600.00 * rate, 2) - (10.00 if month == "2026-07" else 0.0)
        _pdf(folder[month] / f"Kwame Mensah_{month}.pdf", _usd_invoice(f"KM-2026-{n:02d}", last, 30, month, usd, rate))

        # Tomás Herrera: standing -2.50; April first sent wrong (6,000) then corrected;
        # June and September carry the on-call extra; August issued on the 28th.
        issued = last.replace(day=28) if month == "2026-08" else last
        extra_lines, amount = (), 6097.50
        if month in ONCALL:
            extra = oncall.compute_extra(ONCALL[month], 6100.00, oncall.DEFAULT_RATES)["total_eur"]
            amount = round(6097.50 + extra, 2)
            extra_lines = ("Monthly compensation EUR: 6,097.50", f"On Call extra EUR: {extra:,.2f}")
        if month == "2026-04":
            _pdf(folder[month] / f"Tomás Herrera_{month}.pdf", _eur_invoice(f"TH-{n:02d}", issued, 21, month, 6000.00))
            _pdf(folder[month] / "corrected" / f"Tomás Herrera_{month}.pdf",
                 _eur_invoice(f"TH-{n:02d}b", issued + dt.timedelta(days=3), 21, month, amount, supply=last))
            files += 1
        else:
            _pdf(folder[month] / f"Tomás Herrera_{month}.pdf",
                 _eur_invoice(f"TH-{n:02d}", issued, 21, month, amount, extra_lines=extra_lines))
        files += 4

    # A scan that was never renamed: the Validate tab offers to name it.
    _pdf(inv / "scan_0931.pdf", ["Invoice 0931", "Date of issue: 30.09.2026", "Total due EUR: 1,200.00"])
    files += 1

    (ROOT / "oncall").mkdir(exist_ok=True)
    write_attendance_xlsx(ROOT / "oncall" / "demo-attendance.xlsx", {
        _last_day(m).strftime("%b"): {"rows": _weekday_rows(m, "Herrera Tomás", h)} for m, h in ONCALL.items()
    })

    print("Demo data written:")
    print(f"  {len(CONTRACTORS)} contractors with contract terms in {db.DB_PATH}")
    print(f"  {files} invoice PDFs under {inv} ({MONTHS[0]} to {MONTHS[-1]})")
    print(f"  on-call sheet {ROOT / 'oncall' / 'demo-attendance.xlsx'} (tabs: {', '.join(_last_day(m).strftime('%b') for m in ONCALL)})")
    for m, note in rate_notes.items():
        print(f"  USD invoice {m} priced with {note}")
    print("Now run scripts/start_app.sh, import June and September on the On-call tab, then validate.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="replace an existing demo database")
    make_demo(ap.parse_args().force)
