"""Local SQLite storage. Never touches the network. All confidential data
(contractor names, amounts, dates) lives only in this file on local disk."""

from __future__ import annotations

import json
import os
import sqlite3
import unicodedata
from contextlib import contextmanager
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "app.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS contractors (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    due_days INTEGER                -- payment terms from the contract: days from the issue date (NULL = not set)
);

CREATE TABLE IF NOT EXISTS contract_terms (
    id INTEGER PRIMARY KEY,
    contractor_id INTEGER NOT NULL REFERENCES contractors(id),
    amount_eur REAL NOT NULL,
    effective_from TEXT NOT NULL,   -- 'YYYY-MM', inclusive
    effective_to TEXT,             -- 'YYYY-MM', inclusive, NULL = open-ended
    adjustment_eur REAL NOT NULL DEFAULT 0,  -- standing difference the invoice always shows vs. the contract
    adjustment_note TEXT
);

CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY,
    file_sha256 TEXT NOT NULL UNIQUE,
    original_filename TEXT NOT NULL,
    contractor_id INTEGER REFERENCES contractors(id),
    invoice_number TEXT,
    service_month TEXT,             -- 'YYYY-MM'
    service_month_source TEXT,      -- 'filename' | 'text' | 'conflict'
    issue_date TEXT,                -- 'YYYY-MM-DD'
    issue_date_confidence TEXT,
    issue_date_snippet TEXT,
    due_date TEXT,
    payment_date TEXT,
    taxable_supply_date TEXT,       -- 'YYYY-MM-DD' (Czech DUZP); when present, Rule B checks this
    taxable_supply_confidence TEXT,
    taxable_supply_snippet TEXT,
    currency TEXT,
    standard_amount_value REAL,
    standard_amount_confidence TEXT,
    standard_amount_snippet TEXT,
    usd_amount_value REAL,
    usd_amount_confidence TEXT,
    usd_amount_snippet TEXT,
    oncall_present INTEGER NOT NULL DEFAULT 0,
    oncall_snippet TEXT,
    extraction_method TEXT,         -- 'text' | 'ocr'
    processed_at TEXT NOT NULL,     -- last time this file was validated (updated on every run)
    first_seen_at TEXT              -- when this file was first validated (never updated): orders versions
);

CREATE TABLE IF NOT EXISTS validations (
    id INTEGER PRIMARY KEY,
    invoice_id INTEGER NOT NULL UNIQUE REFERENCES invoices(id),
    status TEXT NOT NULL,           -- Pass | Warning | Manual Review | Unable to Verify
    warnings TEXT NOT NULL,         -- JSON list of strings
    expected_eur REAL,
    expected_usd REAL,
    invoiced_usd REAL,
    fx_rate REAL,
    fx_requested_date TEXT,
    fx_effective_date TEXT,
    fx_source TEXT,
    computed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS oncall_hours (
    id INTEGER PRIMARY KEY,
    contractor_id INTEGER NOT NULL REFERENCES contractors(id),
    service_month TEXT NOT NULL,    -- 'YYYY-MM'
    standby_standard REAL NOT NULL DEFAULT 0,
    standby_holiday REAL NOT NULL DEFAULT 0,
    active_evening REAL NOT NULL DEFAULT 0,
    active_night REAL NOT NULL DEFAULT 0,
    active_holiday REAL NOT NULL DEFAULT 0,
    unreadable INTEGER NOT NULL DEFAULT 0,  -- some entry in the sheet couldn't be read
    notes TEXT,                     -- JSON list of strings
    source_file TEXT,
    imported_at TEXT NOT NULL,
    UNIQUE(contractor_id, service_month)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fx_rates (
    effective_date TEXT PRIMARY KEY,  -- 'YYYY-MM-DD', the date we REQUESTED (last day of month); historical name
    rate_usd_per_eur REAL NOT NULL,
    source_url TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    published_date TEXT              -- 'YYYY-MM-DD', the date the ECB actually published the rate for
);
"""


def init_db():
    DATA_DIR.mkdir(exist_ok=True)
    os.chmod(DATA_DIR, 0o700)
    with get_connection() as conn:
        conn.executescript(SCHEMA)
        fx_cols = {r["name"] for r in conn.execute("PRAGMA table_info(fx_rates)")}
        if "published_date" not in fx_cols:
            conn.execute("ALTER TABLE fx_rates ADD COLUMN published_date TEXT")
        term_cols = {r["name"] for r in conn.execute("PRAGMA table_info(contract_terms)")}
        if "adjustment_eur" not in term_cols:
            conn.execute("ALTER TABLE contract_terms ADD COLUMN adjustment_eur REAL NOT NULL DEFAULT 0")
        if "adjustment_note" not in term_cols:
            conn.execute("ALTER TABLE contract_terms ADD COLUMN adjustment_note TEXT")
        inv_cols = {r["name"] for r in conn.execute("PRAGMA table_info(invoices)")}
        for col in ("taxable_supply_date", "taxable_supply_confidence", "taxable_supply_snippet", "first_seen_at"):
            if col not in inv_cols:
                conn.execute(f"ALTER TABLE invoices ADD COLUMN {col} TEXT")
        # Files validated before first_seen_at existed: the best we know is their last run.
        conn.execute("UPDATE invoices SET first_seen_at = processed_at WHERE first_seen_at IS NULL")
        con_cols = {r["name"] for r in conn.execute("PRAGMA table_info(contractors)")}
        if "due_days" not in con_cols:
            conn.execute("ALTER TABLE contractors ADD COLUMN due_days INTEGER")
        val_cols = {r["name"] for r in conn.execute("PRAGMA table_info(validations)")}
        for col in ("oncall_extra_eur", "invoiced_eur"):
            if col not in val_cols:
                conn.execute(f"ALTER TABLE validations ADD COLUMN {col} REAL")
    os.chmod(DB_PATH, 0o600)


@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _name_key(name: str) -> str:
    """Case-, accent- and spacing-insensitive form of a contractor name, so
    'Emile Dupont' or 'Dupont Émile' typed in the Contracts tab and
    'Émile Dupont' from an invoice filename are recognised as the same person."""
    decomposed = unicodedata.normalize("NFKD", name.lower())
    # Word order ignored too: Czech often writes surname first ("Lindqvist Zoë").
    return " ".join(sorted("".join(c for c in decomposed if not unicodedata.combining(c)).split()))


def upsert_contractor(name: str) -> int:
    name = unicodedata.normalize("NFC", name).strip()
    with get_connection() as conn:
        cur = conn.execute("SELECT id FROM contractors WHERE name = ?", (name,))
        row = cur.fetchone()
        if row:
            return row["id"]
        key = _name_key(name)
        for existing in conn.execute("SELECT id, name FROM contractors").fetchall():
            if _name_key(existing["name"]) == key:
                return existing["id"]
        cur = conn.execute("INSERT INTO contractors (name) VALUES (?)", (name,))
        return cur.lastrowid


def set_contractor_due_days(contractor_id: int, days):
    """Payment terms (days from the issue date), or None to clear."""
    with get_connection() as conn:
        conn.execute("UPDATE contractors SET due_days = ? WHERE id = ?",
                     (int(days) if days else None, contractor_id))


def get_contractor_name(contractor_id: int):
    with get_connection() as conn:
        row = conn.execute("SELECT name FROM contractors WHERE id = ?", (contractor_id,)).fetchone()
        return row["name"] if row else None


def list_contractors():
    with get_connection() as conn:
        return conn.execute("SELECT * FROM contractors ORDER BY name").fetchall()


def add_contract_term(
    contractor_id: int, amount_eur: float, effective_from: str, effective_to: str | None,
    adjustment_eur: float = 0.0, adjustment_note: str | None = None,
):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO contract_terms (contractor_id, amount_eur, effective_from, effective_to, adjustment_eur, adjustment_note) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (contractor_id, amount_eur, effective_from, effective_to, adjustment_eur or 0.0, adjustment_note),
        )


def set_term_adjustment(term_id: int, adjustment_eur: float, adjustment_note: str | None):
    with get_connection() as conn:
        conn.execute(
            "UPDATE contract_terms SET adjustment_eur = ?, adjustment_note = ? WHERE id = ?",
            (adjustment_eur or 0.0, adjustment_note, term_id),
        )


def list_contract_terms(contractor_id: int | None = None):
    with get_connection() as conn:
        if contractor_id is None:
            return conn.execute(
                "SELECT ct.*, c.name AS contractor_name FROM contract_terms ct "
                "JOIN contractors c ON c.id = ct.contractor_id "
                "ORDER BY c.name, ct.effective_from"
            ).fetchall()
        return conn.execute(
            "SELECT * FROM contract_terms WHERE contractor_id = ? ORDER BY effective_from",
            (contractor_id,),
        ).fetchall()


def delete_contract_term(term_id: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM contract_terms WHERE id = ?", (term_id,))


def get_effective_contract_terms(contractor_id: int, service_month: str):
    """Return all contract_terms rows whose [effective_from, effective_to] window
    covers service_month ('YYYY-MM'). Zero rows = no contract found; more than
    one row = ambiguous/overlapping terms — caller must treat both as
    Manual Review, never silently pick one."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM contract_terms WHERE contractor_id = ? "
            "AND effective_from <= ? AND (effective_to IS NULL OR effective_to >= ?)",
            (contractor_id, service_month, service_month),
        ).fetchall()


def upsert_invoice(fields: dict) -> int:
    """Insert a new invoice row, or update the existing one with the same
    file_sha256 (reprocessing the same file updates in place rather than
    duplicating). first_seen_at is set on insert only, so the order in which
    versions of an invoice arrived survives any later re-validation."""
    fields = {k: v for k, v in fields.items() if k != "first_seen_at"}
    with get_connection() as conn:
        cur = conn.execute("SELECT id FROM invoices WHERE file_sha256 = ?", (fields["file_sha256"],))
        existing = cur.fetchone()
        if not existing:
            fields["first_seen_at"] = fields["processed_at"]
        cols = list(fields.keys())
        if existing:
            set_clause = ", ".join(f"{c} = ?" for c in cols)
            conn.execute(f"UPDATE invoices SET {set_clause} WHERE id = ?", [fields[c] for c in cols] + [existing["id"]])
            return existing["id"]
        placeholders = ", ".join("?" for _ in cols)
        cur = conn.execute(f"INSERT INTO invoices ({', '.join(cols)}) VALUES ({placeholders})", [fields[c] for c in cols])
        return cur.lastrowid


# --- On-call hours (from the attendance sheet) and app settings ---------------

def upsert_oncall_hours(contractor_id: int, service_month: str, hours: dict, unreadable: bool,
                        notes: list, source_file: str | None, imported_at: str) -> int:
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO oncall_hours (contractor_id, service_month, standby_standard, standby_holiday,
                   active_evening, active_night, active_holiday, unreadable, notes, source_file, imported_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(contractor_id, service_month) DO UPDATE SET
                   standby_standard = excluded.standby_standard, standby_holiday = excluded.standby_holiday,
                   active_evening = excluded.active_evening, active_night = excluded.active_night,
                   active_holiday = excluded.active_holiday, unreadable = excluded.unreadable,
                   notes = excluded.notes, source_file = excluded.source_file, imported_at = excluded.imported_at""",
            (contractor_id, service_month, float(hours.get("standby_standard") or 0), float(hours.get("standby_holiday") or 0),
             float(hours.get("active_evening") or 0), float(hours.get("active_night") or 0),
             float(hours.get("active_holiday") or 0), int(bool(unreadable)), json.dumps(list(notes or [])),
             source_file, imported_at),
        )
        return conn.execute(
            "SELECT id FROM oncall_hours WHERE contractor_id = ? AND service_month = ?", (contractor_id, service_month)
        ).fetchone()["id"]


def _oncall_row(r):
    if r is None:
        return None
    d = dict(r)
    d["notes"] = json.loads(d["notes"]) if d.get("notes") else []
    d["unreadable"] = bool(d["unreadable"])
    return d


def get_oncall_hours(contractor_id: int, service_month: str):
    with get_connection() as conn:
        return _oncall_row(conn.execute(
            "SELECT * FROM oncall_hours WHERE contractor_id = ? AND service_month = ?", (contractor_id, service_month)
        ).fetchone())


def list_oncall_hours():
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT o.*, c.name AS contractor_name FROM oncall_hours o JOIN contractors c ON c.id = o.contractor_id "
            "ORDER BY o.service_month DESC, c.name"
        ).fetchall()
    return [_oncall_row(r) for r in rows]


def delete_oncall_hours(row_id: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM oncall_hours WHERE id = ?", (row_id,))


def get_setting(key: str, default=None):
    with get_connection() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str):
    with get_connection() as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (key, value))


def find_invoice_by_number(contractor_id: int, invoice_number: str):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM invoices WHERE contractor_id = ? AND invoice_number = ?",
            (contractor_id, invoice_number),
        ).fetchall()


def get_invoice_by_hash(file_sha256: str):
    with get_connection() as conn:
        return conn.execute("SELECT * FROM invoices WHERE file_sha256 = ?", (file_sha256,)).fetchone()


def upsert_validation(invoice_id: int, fields: dict):
    fields = dict(fields)
    fields["invoice_id"] = invoice_id
    fields["warnings"] = json.dumps(fields.get("warnings", []))
    with get_connection() as conn:
        cur = conn.execute("SELECT id FROM validations WHERE invoice_id = ?", (invoice_id,))
        existing = cur.fetchone()
        cols = list(fields.keys())
        if existing:
            set_clause = ", ".join(f"{c} = ?" for c in cols)
            conn.execute(f"UPDATE validations SET {set_clause} WHERE id = ?", [fields[c] for c in cols] + [existing["id"]])
        else:
            placeholders = ", ".join("?" for _ in cols)
            conn.execute(f"INSERT INTO validations ({', '.join(cols)}) VALUES ({placeholders})", [fields[c] for c in cols])


def list_dashboard_rows(
    contractor: str | None = None,
    service_month: str | None = None,
    status: str | None = None,
    newest_first: bool = True,
):
    query = """
        SELECT i.*, c.name AS contractor_name, c.due_days, v.status, v.warnings, v.expected_eur,
               v.expected_usd, v.invoiced_usd, v.fx_rate, v.fx_effective_date,
               v.fx_source, v.computed_at, v.oncall_extra_eur, v.invoiced_eur
        FROM invoices i
        LEFT JOIN contractors c ON c.id = i.contractor_id
        LEFT JOIN validations v ON v.invoice_id = i.id
        WHERE 1=1
    """
    params = []
    if contractor:
        query += " AND c.name = ?"
        params.append(contractor)
    if service_month:
        query += " AND i.service_month = ?"
        params.append(service_month)
    if status:
        query += " AND v.status = ?"
        params.append(status)
    direction = "DESC" if newest_first else "ASC"
    query += f" ORDER BY i.service_month {direction}, i.issue_date {direction}, c.name"
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) | {"warnings": json.loads(r["warnings"] or "[]")} for r in rows]


def delete_invoice(invoice_id: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM validations WHERE invoice_id = ?", (invoice_id,))
        conn.execute("DELETE FROM invoices WHERE id = ?", (invoice_id,))


def get_cached_fx_rate(effective_date: str):
    with get_connection() as conn:
        return conn.execute("SELECT * FROM fx_rates WHERE effective_date = ?", (effective_date,)).fetchone()


def cache_fx_rate(requested_date: str, rate: float, source_url: str, retrieved_at: str, published_date: str):
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO fx_rates (effective_date, rate_usd_per_eur, source_url, retrieved_at, published_date) "
            "VALUES (?, ?, ?, ?, ?)",
            (requested_date, rate, source_url, retrieved_at, published_date),
        )
