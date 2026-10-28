"""Confidentiality guarantees and database upgrade safety."""

from tests._guard import refuse_real_database  # noqa: F401  (stops tests touching the real DB)
import datetime
import os
import re
import sqlite3
import urllib.request
from pathlib import Path

from pipeline import db, fx

PROJECT = Path(__file__).resolve().parent.parent
APP_CODE = [PROJECT / "app.py", PROJECT / "auth.py", PROJECT / "ui_style.py", *sorted((PROJECT / "pipeline").glob("*.py"))]


# --- Confidentiality --------------------------------------------------------

def test_server_is_local_only_and_telemetry_off():
    config = (PROJECT / ".streamlit" / "config.toml").read_text()
    assert re.search(r'^address\s*=\s*"127\.0\.0\.1"\s*$', config, re.M)
    assert re.search(r"^gatherUsageStats\s*=\s*false\s*$", config, re.M)
    assert re.search(r"^enableXsrfProtection\s*=\s*true\s*$", config, re.M)


def test_only_the_fx_module_contains_network_code():
    network = re.compile(r"\b(urllib\.request|urlopen|requests\.|http\.client|socket\.|smtplib|ftplib)\b")
    offenders = [
        f"{path.name}:{n}"
        for path in APP_CODE
        if path.name != "fx.py"
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if network.search(line) and not line.lstrip().startswith("#")
    ]
    assert offenders == []


def test_ecb_request_carries_only_a_date(monkeypatch):
    seen = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return (b"KEY,FREQ,CURRENCY,CURRENCY_DENOM,EXR_TYPE,EXR_SUFFIX,TIME_PERIOD,OBS_VALUE,OBS_STATUS\n"
                    b"EXR.D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,2026-09-29,1.0812,A\n"
                    b"EXR.D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,2026-09-30,1.0850,A\n")

    def fake_urlopen(req, timeout):
        seen["url"] = req.full_url
        seen["data"] = req.data
        seen["headers"] = dict(req.header_items())
        return FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    result = fx.fetch_rate_for_date(datetime.date(2026, 9, 30))
    assert result["rate"] == 1.085 and result["effective_date"] == "2026-09-30"
    assert seen["url"] == f"{fx.ECB_URL}?startPeriod=2026-09-20&endPeriod=2026-09-30&format=csvdata"
    assert seen["url"].startswith("https://data-api.ecb.europa.eu/")
    assert seen["data"] is None  # GET, no body
    assert set(k.lower() for k in seen["headers"]) == {"user-agent"}


def test_gitignore_keeps_confidential_files_out_of_git():
    ignored = set((PROJECT / ".gitignore").read_text().split())
    assert {"invoices/", "data/", "exports/", "secrets.local.json", "*.pdf"} <= ignored


def test_database_files_are_owner_only(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "data" / "app.db")
    db.init_db()
    assert oct(os.stat(tmp_path / "data").st_mode)[-3:] == "700"
    assert oct(os.stat(tmp_path / "data" / "app.db").st_mode)[-3:] == "600"


def test_test_fixtures_contain_no_real_contractor_names():
    """Real names are read at runtime from the local database (read-only), so
    they never have to be written into the test code itself."""
    real_db = PROJECT / "data" / "app.db"
    if not real_db.exists():
        return
    conn = sqlite3.connect(f"file:{real_db}?mode=ro", uri=True)
    try:
        if _guard_module().is_demo_database(conn):
            return  # filled by scripts/make_demo.py: fictional names, allowed in tests
        names = [r[0] for r in conn.execute("SELECT name FROM contractors")]
    finally:
        conn.close()
    surnames = {n.split()[-1].lower() for n in names if n.split()}
    fixture_dir = PROJECT / "tests" / "fixtures"
    test_sources = [*fixture_dir.glob("*.py"), *(PROJECT / "tests").glob("*.py")]
    from pipeline.extraction import extract_text, normalize
    texts = [normalize(p.read_text()) for p in test_sources]
    texts += [normalize(extract_text(p)[0] + " " + p.name) for p in fixture_dir.glob("*.pdf")]
    leaked = sorted({s for s in surnames for t in texts if normalize(s) in t})
    assert leaked == [], "real contractor surnames found in test files"


def _guard_module():
    """scripts/check_no_confidential.py loaded as a module (scripts/ is not a package)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("check_no_confidential", PROJECT / "scripts" / "check_no_confidential.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pre_commit_guard_ignores_a_demo_database(tmp_path, monkeypatch):
    """Names planted by scripts/make_demo.py are fictional and must not block
    commits; names in any other database are treated as real."""
    guard = _guard_module()
    monkeypatch.setattr(db, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "data" / "app.db")
    db.init_db()
    db.upsert_contractor("Someone Realistic")
    monkeypatch.setattr(guard, "ROOT", tmp_path)
    assert guard.real_surnames() == {"realistic"}
    db.set_setting("demo_data", "1")
    assert guard.real_surnames() == set()


# --- Database upgrades ------------------------------------------------------

OLD_SCHEMA = """
CREATE TABLE contractors (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE contract_terms (id INTEGER PRIMARY KEY, contractor_id INTEGER NOT NULL REFERENCES contractors(id),
    amount_eur REAL NOT NULL, effective_from TEXT NOT NULL, effective_to TEXT);
CREATE TABLE invoices (id INTEGER PRIMARY KEY, file_sha256 TEXT NOT NULL UNIQUE, original_filename TEXT NOT NULL,
    contractor_id INTEGER REFERENCES contractors(id), invoice_number TEXT, service_month TEXT, service_month_source TEXT,
    issue_date TEXT, issue_date_confidence TEXT, issue_date_snippet TEXT, due_date TEXT, payment_date TEXT, currency TEXT,
    standard_amount_value REAL, standard_amount_confidence TEXT, standard_amount_snippet TEXT, usd_amount_value REAL,
    usd_amount_confidence TEXT, usd_amount_snippet TEXT, oncall_present INTEGER NOT NULL DEFAULT 0, oncall_snippet TEXT,
    extraction_method TEXT, processed_at TEXT NOT NULL);
CREATE TABLE validations (id INTEGER PRIMARY KEY, invoice_id INTEGER NOT NULL UNIQUE REFERENCES invoices(id),
    status TEXT NOT NULL, warnings TEXT NOT NULL, expected_eur REAL, expected_usd REAL, invoiced_usd REAL, fx_rate REAL,
    fx_requested_date TEXT, fx_effective_date TEXT, fx_source TEXT, computed_at TEXT NOT NULL);
CREATE TABLE fx_rates (effective_date TEXT PRIMARY KEY, rate_usd_per_eur REAL NOT NULL, source_url TEXT NOT NULL,
    retrieved_at TEXT NOT NULL);
INSERT INTO contractors VALUES (1, 'Old Example');
INSERT INTO contract_terms VALUES (1, 1, 50000, '2026-01', NULL);
INSERT INTO invoices (id, file_sha256, original_filename, contractor_id, processed_at) VALUES (1, 'abc', 'f.pdf', 1, 'x');
INSERT INTO validations VALUES (1, 1, 'Pass', '[]', NULL, NULL, NULL, NULL, NULL, NULL, NULL, 'x');
INSERT INTO fx_rates VALUES ('2026-01-31', 1.08, 'u', 'r');
"""


def test_upgrading_an_old_database_keeps_data(tmp_path, monkeypatch):
    path = tmp_path / "old" / "app.db"
    path.parent.mkdir()
    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA)
    conn.close()
    monkeypatch.setattr(db, "DATA_DIR", path.parent)
    monkeypatch.setattr(db, "DB_PATH", path)

    db.init_db()
    db.init_db()  # running the upgrade twice must be harmless
    assert db.list_dashboard_rows()[0]["first_seen_at"] == "x"  # backfilled from processed_at
    assert db.list_dashboard_rows()[0]["due_days"] is None  # new column added to an old contractors table
    db.set_contractor_due_days(1, 30)
    assert db.list_contractors()[0]["due_days"] == 30
    db.set_contractor_due_days(1, 0)
    assert db.list_contractors()[0]["due_days"] is None

    with db.get_connection() as c:
        cols = lambda t: {r["name"] for r in c.execute(f"PRAGMA table_info({t})")}
        assert {"adjustment_eur", "adjustment_note"} <= cols("contract_terms")
        assert {"taxable_supply_date", "taxable_supply_confidence", "taxable_supply_snippet"} <= cols("invoices")
        assert "published_date" in cols("fx_rates")
    assert db.list_contract_terms()[0]["adjustment_eur"] == 0
    assert db.list_dashboard_rows()[0]["status"] == "Pass"
    assert db.get_contractor_name(1) == "Old Example"


def test_old_fx_cache_row_is_refetched_not_trusted(tmp_path, monkeypatch):
    with db.get_connection() as c:
        c.execute("INSERT INTO fx_rates (effective_date, rate_usd_per_eur, source_url, retrieved_at) "
                  "VALUES ('2026-01-31', 99.0, 'u', 'r')")
    monkeypatch.setattr(fx, "_fetch_raw", lambda d: "TIME_PERIOD,OBS_VALUE\n2026-01-30,1.0413\n")
    r = fx.get_rate_for_month(2026, 1)
    assert r["rate"] == 1.0413 and r["effective_date"] == "2026-01-30"


def test_deleting_an_invoice_removes_its_validation(tmp_path):
    cid = db.upsert_contractor("Del Example")
    iid = db.upsert_invoice({"file_sha256": "h", "original_filename": "x.pdf", "contractor_id": cid, "processed_at": "t"})
    db.upsert_validation(iid, {"status": "Pass", "warnings": [], "computed_at": "t"})
    db.delete_invoice(iid)
    with db.get_connection() as c:
        assert c.execute("SELECT COUNT(*) FROM validations").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 0


def test_styling_loads_nothing_from_the_internet():
    """The burgundy theme is local CSS + emoji only: no web fonts, remote images
    or scripts, so opening the app never contacts another server."""
    for path in [PROJECT / "app.py", PROJECT / "ui_style.py"]:
        text = path.read_text()
        assert not re.search(r"https?://", text), path.name
        assert "@import" not in text and "fonts.googleapis" not in text
        # Only inline scripts written here (the idle-logout timer) — never a script loaded from elsewhere
        assert not re.search(r"<script[^>]*\bsrc\s*=", text, re.I), path.name
        assert "fetch(" not in text and "XMLHttpRequest" not in text and "WebSocket(" not in text


def test_guard_refuses_the_real_database(monkeypatch):
    import pytest
    from tests import _guard
    monkeypatch.setattr(db, "DB_PATH", _guard.REAL_DB)
    monkeypatch.setattr(db, "DATA_DIR", _guard.REAL_DB.parent)
    with pytest.raises(RuntimeError, match="REAL database"):
        _guard.check_not_real_db()


def test_every_test_module_imports_the_guard():
    for path in (PROJECT / "tests").glob("test_*.py"):
        assert "refuse_real_database" in path.read_text(), path.name
