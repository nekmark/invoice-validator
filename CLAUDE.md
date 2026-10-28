# Invoice Validator — notes for Claude

Local-only Streamlit + SQLite app that validates contractor invoices (see
README.md for the rules and layout). The repository holds only code, tests
and fictional demo data; anyone running it with real invoices keeps those in
the gitignored folders below.

## Confidentiality

- `invoices/`, `data/`, `exports/`, `oncall/` and `secrets.local.json` may
  hold real data on a user's machine. Never read, print or screenshot their
  contents; when something must be diagnosed, print counts, statuses or
  shapes (digits → `9`, letters → `a`), never values.
- Never put real contractor names or amounts into code, comments, tests,
  commit messages or docs. Use made-up names (e.g. "Émile Dupont",
  "Zoë Lindqvist"). `scripts/check_no_confidential.py` (pre-commit) and
  `tests/test_security_and_db.py` enforce this against the local database.
- The only allowed network call is `pipeline/fx.py` → the ECB Data Portal,
  with nothing but a date range. Do not add web fonts, CDNs, telemetry or
  cloud OCR/LLM APIs.
- Back up the DB (`pipeline/backup.py`) before any bulk change to real data.

## Working on the code

```bash
source venv/bin/activate
python -m pytest tests/ -q            # must stay green; no network, temp DB per test
```

- **Run tests only as `python -m pytest tests/` from the project folder.**
  `tests/conftest.py` points every test at a throwaway database and blocks
  the network; `tests/_guard.py` refuses to run against `data/app.db`.
- Business rules: an invoice may be up to `ACCEPTABLE_DIFFERENCE_EUR` (5 EUR)
  *below* the contract, never above beyond cent rounding; a stated date of
  taxable supply must be the month's last day, otherwise the issue date is
  checked; terms can carry a standing adjustment; the on-call extra (Rule D,
  `pipeline/oncall.py`) is contract ÷ 20 days ÷ 8 hours × hours × multiplier
  per kind, and the invoice total must equal contract + extra however the
  extra is labelled. Exchange rates are USD per EUR (ECB convention).
- Names are stored first-name-first (matching file names) and **displayed**
  surname-first; matching ignores accents, case and word order.
- UI styling lives in `ui_style.py` — local CSS + emoji only. Privacy mode
  (`ui_privacy.py`) masks names/amounts; any new screen that shows names,
  amounts or invoice text must go through `view.who`, `view.amount`,
  `view.text` or `view.snippet`.
- Reinstall the pre-commit hook if it is missing:

  ```bash
  printf '#!/bin/sh\ncd "$(git rev-parse --show-toplevel)" || exit 1\nexec venv/bin/python -I scripts/check_no_confidential.py\n' > .git/hooks/pre-commit
  chmod +x .git/hooks/pre-commit
  ```

## Running the app

`scripts/start_app.sh` serves http://127.0.0.1:8501 (localhost only).
`scripts/make_demo.py` fills an empty install with fictional data.
