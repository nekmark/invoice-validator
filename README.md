# Invoice Validator

A small, local-only web app that checks contractor invoices against their
contracts, month by month. Drop the PDFs into a folder, press **Validate**,
and get a Pass / Warning / Manual Review verdict per invoice with the reason
spelled out. Built with Streamlit and SQLite; runs entirely on your machine.

> Everything in this repository is fictional demo data. Nothing here is a real
> contractor, contract or invoice.

## What it checks

- **Rule A — amount.** The invoiced total equals the contracted monthly
  amount in EUR for that month. Contract terms are effective-dated, so a
  raise in March is handled correctly. A term can carry a *standing
  adjustment* for a known, fixed difference.
- **Rule B — date.** The date of taxable supply, when the invoice states one,
  is the last calendar day of the invoiced month. Invoices without that date
  are checked on their issue date instead.
- **Rule C — currency.** When the invoice is billed in USD, the USD total
  equals the EUR amount converted at the European Central Bank reference
  rate for the last day of the month.
- **Rule D — on-call extra.** If you import an on-call attendance sheet,
  the expected total becomes contract + on-call extra: hourly rate (contract
  ÷ 20 days ÷ 8 hours) × hours × a multiplier per kind of hour (standby,
  standby on a holiday, active work in the evening, after 10 pm, on a holiday).

**Accepted underpayment:** an invoice up to **5 EUR below** the contract is
not treated as a mistake; it passes with a note showing the gap. Anything
*above* the contract beyond cent rounding is a Warning. The limit is
`ACCEPTABLE_DIFFERENCE_EUR` in `pipeline/validate.py`.

### Statuses

| Status | Meaning |
|---|---|
| **Pass** | every applicable check passed |
| **Warning** | a real discrepancy: amount, date or currency |
| **Manual Review** | something couldn't be read or decided reliably (no contract term, ambiguous dates, an On Call amount that can't be separated, a duplicate invoice number, …) |
| **Unable to Verify** | the ECB rate couldn't be fetched or didn't pass its sanity check; the app never guesses a rate |

## Try it with demo data

```bash
git clone <this repo> invoice-validator && cd invoice-validator
python3 -m venv venv && source venv/bin/activate
pip install -r requirements-dev.txt      # app + pytest + reportlab (the demo PDFs are generated with reportlab)
python scripts/make_demo.py              # fictional contractors, invoices and an on-call sheet
scripts/start_app.sh                     # opens http://127.0.0.1:8501
```

On first run choose an app password. Then open the **📟 On-call** tab and
import June and September, go to **✨ Validate** and press *Validate all
invoices*. The demo covers March to September 2026 for four contractors:
most months pass, and scattered through them are an invoice a few euros
short (accepted with a note), two issued on the wrong day, a contract raise
the contractor forgot to invoice, an overcharge, USD invoices at the real
ECB rate with one 10 USD short, two months with on-call extras, a wrong
invoice followed by a corrected version, and one file that still needs
naming. Enough for the History and Report tabs to have something to say.

Optional, for scanned PDFs: `brew install tesseract poppler` (macOS). Without
them, image-only PDFs are flagged Manual Review instead of being OCR'd.

## Using it for real

1. Put the invoice PDFs in `invoices/` (subfolders such as `invoices/2026-09/`
   are fine). Only the **file name** carries meaning:

   ```
   <Contractor Name>_<YYYY-MM>.pdf        e.g.  Ana Pereira_2026-09.pdf
   ```

   `YYYY-MM` is the invoiced service month. Files that don't follow the
   pattern are listed on the Validate tab with a suggested name you can
   accept with one click.
2. On the **📑 Contracts** tab, give each contractor a monthly amount and the
   months it applies to (and payment terms, if you want due dates shown).
3. If you pay on-call extras, drop the attendance xlsx into `oncall/` and
   import the month on the **📟 On-call** tab (one tab per month, one row per
   day, a `Name` column per person on call, a green row = national holiday;
   names that aren't contractors are treated as staff and skipped).
4. **✨ Validate.** Results land on the **📊 Dashboard** with a detail view
   that shows exactly what was read from the PDF and how the expected amount
   was computed. **🗓️ History** shows everyone month by month and the gap
   between invoice and contract; **📋 Report** counts who got it right first
   time and how many corrected versions had to be sent.

The **🙈 Privacy** switch in the header masks names and amounts for screen
sharing. A backup of the database is taken once a day (and before anything
destructive); restore from the Validate tab.

## Privacy and security

The app is meant for confidential data, so it is built to keep it on your
machine:

- The server binds to `127.0.0.1` only and Streamlit telemetry is off.
- The **only** network call in the code base is in `pipeline/fx.py`: a date
  range sent to the public ECB Data Portal API. No invoice content, name or
  amount ever leaves the machine; a test enforces that no other module
  contains networking code.
- No web fonts, CDNs or remote scripts: the UI is local CSS and emoji.
- A local password gates the UI (PBKDF2 hash in `secrets.local.json`,
  gitignored). Logins last 15 minutes and end after 30 seconds without
  activity; the session token lives in a `SameSite=Strict` cookie, never in
  the URL.
- `invoices/`, `data/`, `exports/`, `oncall/` and all PDFs, spreadsheets and
  databases are gitignored. `data/` and exports are created owner-only.
- `scripts/check_no_confidential.py` is a pre-commit hook that refuses to
  commit those paths or any contractor surname found in the local database:

  ```bash
  printf '#!/bin/sh\ncd "$(git rev-parse --show-toplevel)" || exit 1\nexec venv/bin/python -I scripts/check_no_confidential.py\n' > .git/hooks/pre-commit
  chmod +x .git/hooks/pre-commit
  ```

## How it works

```
invoices/*.pdf ──► pipeline/extraction.py ──► pipeline/validate.py ──► data/app.db ──► app.py (Streamlit)
                   pdfplumber text layer,       Rules A–D,                SQLite           Validate · Dashboard ·
                   Tesseract OCR fallback       ECB rate via fx.py                         History · Report ·
                                                on-call via oncall.py                      On-call · Contracts
```

Extraction is keyword/regex based and deliberately conservative: when a
field can't be found under an unambiguous label, or candidates conflict, the
invoice goes to Manual Review rather than being guessed. Labels are
recognised in English and Czech (e.g. *Date of issue* / *Datum vystavení*),
and amounts in both `4,000.00` and `4 000,00` styles. Contractor names are
matched ignoring accents, case and word order, stored as in the file name
and displayed surname-first.

## Tests

```bash
source venv/bin/activate
pip install -r requirements-dev.txt
python tests/fixtures/make_fixtures.py   # regenerate the synthetic test invoices
python -m pytest tests/ -q
```

The suite never touches the network or real data: every test gets a
throwaway database, any attempt to open a network connection fails the
test, and exchange-rate lookups are mocked. It covers extraction of several
real-world invoice layouts end to end, every validation rule and its edge
cases, the on-call calculator and sheet reader, the ECB client, naming,
backups, authentication, privacy mode and the Streamlit UI itself (driven
through `streamlit.testing`).

## Project layout

```
app.py              Streamlit UI (tabs, login, privacy mode)
auth.py             local password + remembered sessions
ui_style.py         theme (local CSS only)
ui_privacy.py       masking for screen sharing
pipeline/
  extraction.py     PDF → fields (pdfplumber, optional Tesseract OCR)
  validate.py       Rules A–D and statuses
  fx.py             ECB reference rate (the only network call)
  oncall.py         on-call sheet reader + extra-pay calculator
  db.py             SQLite schema and queries
  history.py, report.py, naming.py, contracts.py, dedupe.py, backup.py
scripts/
  start_app.sh      run the app on 127.0.0.1:8501
  make_demo.py      fictional demo data
  check_no_confidential.py   pre-commit guard
tests/              pytest suite + synthetic fixtures
```
