"""Interface tests: drive the real Streamlit app (app.py) through every tab
with Streamlit's AppTest. Each test runs against a throwaway copy of the app
in tmp_path, so invoices/, exports/, the DB, the password file and login
sessions are all temporary. FX is mocked; no network access.
"""

from tests._guard import refuse_real_database  # noqa: F401  (stops tests touching the real DB)
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import streamlit.runtime.context as st_context
from streamlit.testing.v1 import AppTest

import auth
from pipeline import db, fx

PROJECT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
FAKE_FX = {
    "rate": 1.085,
    "effective_date": "2026-01-30",
    "requested_date": "2026-01-31",
    "source_url": "https://example.invalid/ecb",
    "retrieved_at": "2026-02-01T00:00:00",
    "from_cache": True,
}
TITLE = "Invoice Validator"


@pytest.fixture
def app_dir(tmp_path, monkeypatch):
    d = tmp_path / "app"
    (d / "invoices").mkdir(parents=True)
    shutil.copy(PROJECT / "app.py", d / "app.py")
    for pdf in FIXTURES.glob("*.pdf"):
        shutil.copy(pdf, d / "invoices" / pdf.name)
    monkeypatch.setattr(auth, "SECRETS_PATH", tmp_path / "secrets.local.json")
    monkeypatch.setattr(auth, "SESSIONS_PATH", tmp_path / "sessions.json")
    monkeypatch.setattr(fx, "get_rate_for_month", lambda y, m: FAKE_FX)
    # AppTest has no browser, so the cookies "sent" with each run come from
    # this jar (st.context.cookies reads them through _get_client_context).
    BROWSER_COOKIES.clear()
    monkeypatch.setattr(st_context, "_get_client_context",
                        lambda: SimpleNamespace(cookies=dict(BROWSER_COOKIES), headers={}))
    return d


BROWSER_COOKIES = {}


def _app(app_dir, logged_in=True, token=None, **query):
    """Run the app as a browser would open it: with the session cookie from
    a real login (logged_in=True creates one), a given cookie token, or none.
    There is no server-side bypass — the app only ever trusts auth.py."""
    at = AppTest.from_file(str(app_dir / "app.py"), default_timeout=60)
    if logged_in and token is None:
        token = auth.create_session()
    BROWSER_COOKIES.pop(auth.COOKIE_NAME, None)
    if token is not None:
        BROWSER_COOKIES[auth.COOKIE_NAME] = token
    for k, v in query.items():
        at.query_params[k] = v
    return at.run()


def _issued_token(at):
    """The session token the app handed the browser after a login."""
    return at.session_state["auth_token"]


def _button(at, label):
    return next(b for b in at.button if b.label == label)


def _messages(at):
    return [m.value for m in [*at.success, *at.error, *at.info, *at.warning]]


def _title(at):
    return [t.value for t in at.title]


def _results(at):
    """The Dashboard results table (identified by its columns, not position)."""
    return next(d.value for d in at.dataframe if "Service Month" in d.value.columns)


def _charts(at):
    # Streamlit renamed the element ("arrow_vega_lite_chart" -> "vega_lite_chart")
    return [*at.get("vega_lite_chart"), *at.get("arrow_vega_lite_chart")]


def _no_errors(at):
    assert not at.exception, [e.value for e in at.exception]


# --- Login ------------------------------------------------------------------

def test_first_run_sets_password_and_logs_in(app_dir):
    at = _app(app_dir, logged_in=False)
    assert any("No app password" in m for m in _messages(at))
    at.text_input[0].set_value("secret-1")
    at.text_input[1].set_value("different")
    at.button[0].click().run()
    assert any("don't match" in m for m in _messages(at))
    assert not auth.is_configured()
    at.text_input[0].set_value("secret-1")
    at.text_input[1].set_value("secret-1")
    at.button[0].click().run()
    _no_errors(at)
    assert TITLE in _title(at)
    assert auth.is_configured()
    assert "secret-1" not in auth.SECRETS_PATH.read_text()  # only a hash is stored
    assert oct(os.stat(auth.SECRETS_PATH).st_mode)[-3:] == "600"


def test_login_session_survives_refresh_until_logout(app_dir):
    auth.set_password("secret-1")
    at = _app(app_dir, logged_in=False)
    assert _title(at)[0].endswith("Login")
    at.text_input[0].set_value("wrong")
    at.button[0].click().run()
    assert any("Incorrect password" in m for m in _messages(at))
    at.text_input[0].set_value("secret-1")
    at.button[0].click().run()
    assert TITLE in _title(at)
    token = _issued_token(at)
    assert not at.query_params  # the token never goes into the page URL

    refreshed = _app(app_dir, logged_in=False, token=token)  # cookie only, like a browser refresh
    assert TITLE in _title(refreshed)
    assert _title(_app(app_dir, logged_in=False))[0].endswith("Login")  # no cookie -> password
    assert _title(_app(app_dir, logged_in=False, token="forged"))[0].endswith("Login")
    assert _title(_app(app_dir, logged_in=False, s=token))[0].endswith("Login")  # old URL style is dead

    _button(refreshed, "Log out").click().run()
    assert _title(_app(app_dir, logged_in=False, token=token))[0].endswith("Login")


def test_no_server_side_login_bypass(app_dir):
    """session_state['authenticated'] alone must never open the app."""
    auth.set_password("secret-1")
    at = AppTest.from_file(str(app_dir / "app.py"), default_timeout=60)
    at.session_state["authenticated"] = True
    at.run()
    assert _title(at)[0].endswith("Login")
    assert not at.session_state["authenticated"]


# --- Validate tab -----------------------------------------------------------

def test_validate_all_invoices(app_dir):
    db.add_contract_term(db.upsert_contractor("Test Contractor"), 4000.0, "2026-01", None)
    at = _app(app_dir)
    assert any(s.value.startswith("Name files") for s in at.subheader)  # not_a_valid_filename.pdf
    _button(at, "Validate all invoices").click().run()
    _no_errors(at)
    assert any("Processed 7" in m for m in _messages(at))
    assert not any("ERROR" in m.value for m in at.markdown)
    statuses = {r["original_filename"]: r["status"] for r in db.list_dashboard_rows()}
    assert statuses == {
        "Test Contractor_2026-01.pdf": "Pass",
        "Test Contractor_2026-02.pdf": "Warning",        # amount mismatch
        "Test Contractor_2026-03.pdf": "Warning",        # wrong issue date
        "Test Contractor_2026-04.pdf": "Manual Review",  # On Call line, but no on-call hours imported
        "Test Contractor_2026-05.pdf": "Manual Review",  # on-call not separable
        "Test Contractor_2026-06.pdf": "Manual Review",  # no issue date
        "not_a_valid_filename.pdf": "Manual Review",
    }
    # Validating again changes nothing
    _button(at, "Validate all invoices").click().run()
    assert {r["original_filename"]: r["status"] for r in db.list_dashboard_rows()} == statuses


def test_pdfs_in_month_subfolders_are_found_and_renamed_in_place(app_dir):
    sub = app_dir / "invoices" / "September 26"
    sub.mkdir()
    for name in ("Test Contractor_2026-01.pdf", "not_a_valid_filename.pdf"):
        (app_dir / "invoices" / name).rename(sub / name)
    db.add_contract_term(db.upsert_contractor("Test Contractor"), 4000.0, "2026-01", None)
    at = _app(app_dir)
    assert any("7 PDF(s)" in m.value and "September 26" in m.value for m in at.markdown)
    rename_table = next(d.value for d in at.dataframe if "File" in d.value.columns)
    assert list(rename_table["File"]) == ["September 26/not_a_valid_filename.pdf"]
    # The newest folder is preselected: only its 2 PDFs are validated.
    assert at.selectbox(key="validate_folder").value == "September 26"
    _button(at, "Validate September 26").click().run()
    _no_errors(at)
    assert any("Processed 2" in m for m in _messages(at))
    assert {r["original_filename"]: r["status"] for r in db.list_dashboard_rows()} == {
        "Test Contractor_2026-01.pdf": "Pass", "not_a_valid_filename.pdf": "Manual Review"}
    at.selectbox(key="validate_folder").set_value("All folders").run()
    _button(at, "Validate all invoices").click().run()
    assert any("Processed 7" in m for m in _messages(at))


def test_rename_section_only_lists_unrecognised_files(app_dir):
    at = _app(app_dir)
    # AppTest reports st.data_editor as a dataframe; nothing is validated yet,
    # so the only table on the page is the rename table.
    assert len(at.dataframe) == 1, "rename table should be shown"
    _button(at, "Rename ticked files").click().run()
    assert any("nothing was renamed" in m for m in _messages(at))
    assert (app_dir / "invoices" / "not_a_valid_filename.pdf").exists()
    (app_dir / "invoices" / "not_a_valid_filename.pdf").unlink()
    at = _app(app_dir)
    assert not any(s.value.startswith("Name files") for s in at.subheader)
    assert len(at.dataframe) == 0


# --- Contracts tab ----------------------------------------------------------

def test_contracts_add_select_save_overlap_adjust_delete(app_dir):
    at = _app(app_dir)
    for typed, shown in [("Zoe Example", "Zoe Example"), ("Zoë Lindqvist", "Zoë Lindqvist"),
                         ("Lindqvist zoe", "Zoë Lindqvist")]:
        at.text_input(key="new_contractor_name").set_value(typed)
        _button(at, "Add contractor").click().run()
        _no_errors(at)
        assert at.selectbox(key="contracts_selected").value == shown
    assert len(db.list_contractors()) == 2  # surname-first, unaccented name matched the existing one

    at.number_input(key="term_amount").set_value(50000.0)
    at.text_input(key="term_from").set_value("2026, 9")
    _button(at, "Save term for Lindqvist Zoë").click().run()
    assert any("Saved for Lindqvist Zoë: 2026-09 to open-ended" in m for m in _messages(at))

    at.number_input(key="term_amount").set_value(60000.0)
    at.text_input(key="term_from").set_value("2026-12")
    _button(at, "Save term for Lindqvist Zoë").click().run()
    assert any("overlaps" in m for m in _messages(at))

    at.number_input(key="term_amount").set_value(0.0)
    at.text_input(key="term_from").set_value("2030-01")
    _button(at, "Save term for Lindqvist Zoë").click().run()
    assert any("greater than 0" in m for m in _messages(at))

    at.number_input(key="term_amount").set_value(1000.0)
    at.text_input(key="term_from").set_value("September")
    _button(at, "Save term for Lindqvist Zoë").click().run()
    assert any("must be a month" in m for m in _messages(at))
    assert len(db.list_contract_terms()) == 1

    term = db.list_contract_terms()[0]
    at.number_input(key=f"adj_{term['id']}").set_value(-2.15)
    at.text_input(key=f"adj_note_{term['id']}").set_value("rounding")
    _button(at, "Save adjustment").click().run()
    assert db.list_contract_terms()[0]["adjustment_eur"] == -2.15
    assert any("49,997.85 EUR" in m.value for m in at.markdown)

    _button(at, "Delete").click().run()
    assert len(db.list_contract_terms()) == 1  # nothing deleted without confirmation
    assert any("Delete the term 2026-09 to open-ended?" in m for m in _messages(at))
    _button(at, "Cancel").click().run()
    assert len(db.list_contract_terms()) == 1
    _button(at, "Delete").click().run()
    _button(at, "Yes, delete term").click().run()
    _no_errors(at)
    assert db.list_contract_terms() == []


def test_switching_contractor_keeps_terms_separate(app_dir):
    a = db.upsert_contractor("Alpha Example")
    b = db.upsert_contractor("Beta Example")
    at = _app(app_dir)
    at.selectbox(key="contracts_selected").set_value("Beta Example").run()
    at.number_input(key="term_amount").set_value(1234.0)
    at.text_input(key="term_from").set_value("2026-09")
    _button(at, "Save term for Example Beta").click().run()
    assert db.list_contract_terms(a) == []
    assert len(db.list_contract_terms(b)) == 1


# --- Dashboard tab ----------------------------------------------------------

def test_contracts_overview_shows_monthly_manday_and_hour(app_dir, monkeypatch):
    import datetime as _dt
    at = _validated_app(app_dir)  # contract 4,000 EUR from 2026-01, open-ended
    table = next(d.value for d in at.dataframe if "Manday EUR" in d.value.columns)
    row = table.to_dict("records")[0]
    assert row["Contractor"] == "Contractor Test" and row["Monthly EUR"] == "4,000.00"
    assert row["Manday EUR"] == "200.00" and row["Hour EUR"] == "25.00"
    assert row["Valid"] == "2026-01 → open-ended" and row["Terms"] == 1 and row["Adjustment EUR"] == ""
    at.toggle(key="privacy_mode").set_value(True).run()
    table = next(d.value for d in at.dataframe if "Manday EUR" in d.value.columns)
    assert table.to_dict("records")[0]["Monthly EUR"] == ui_privacy_mask() and table.to_dict("records")[0]["Contractor"] == "Contractor A"


def test_payment_terms_give_a_contract_due_date(app_dir):
    at = _validated_app(app_dir)
    cid = db.upsert_contractor("Test Contractor")
    at.selectbox(key="contracts_selected").set_value("Test Contractor").run()
    at.number_input(key=f"due_days_{cid}").set_value(30).run()
    _button(at, "Save payment terms").click().run()
    _no_errors(at)
    assert any("Payment terms saved: 30 days" in m for m in _messages(at))
    assert db.list_contractors()[0]["due_days"] == 30
    dash = _results(at)
    with_contractor = dash[dash["Contractor"] != "—"]
    assert set(with_contractor["Due the contract"]) == {"30 days"}
    assert set(dash[dash["Contractor"] == "—"]["Due the contract"]) == {"—"}  # unrecognised file
    at.number_input(key=f"due_days_{cid}").set_value(0).run()
    _button(at, "Save payment terms").click().run()
    assert set(_results(at)["Due the contract"]) == {"—"}


def test_dashboard_tiles_are_buttons_that_filter_by_status(app_dir):
    at = _validated_app(app_dir)
    tile = next(b for b in at.button if b.key == "tilebtn_warning")
    assert tile.label.startswith("🚩 Warning") and tile.label.endswith("**2**")
    tile.click().run()
    _no_errors(at)
    assert at.selectbox(key="dash_status").value == "Warning"
    assert set(_results(at)["Status"].str.endswith("Warning")) == {True}
    assert next(b for b in at.button if b.key == "tilebtn_warning").proto.type == "primary"  # the active tile is marked
    next(b for b in at.button if b.key == "tilebtn_all_invoices").click().run()
    assert at.selectbox(key="dash_status").value == "All" and len(_results(at)) == 7


def test_dashboard_filter_detail_export_delete(app_dir):
    db.add_contract_term(db.upsert_contractor("Test Contractor"), 4000.0, "2026-01", None)
    at = _app(app_dir)
    _button(at, "Validate all invoices").click().run()
    assert any("File" in d.value.columns for d in at.dataframe)  # rename table (not_a_valid_filename.pdf)
    assert len(_results(at)) == 7

    status = next(s for s in at.selectbox if s.label == "Status")
    status.set_value("Manual Review").run()
    _no_errors(at)
    status = next(s for s in at.selectbox if s.label == "Status")
    status.set_value("All").run()

    _button(at, "Export visible results to CSV").click().run()
    exports = list((app_dir / "exports").glob("*.csv"))
    assert len(exports) == 1
    assert oct(os.stat(exports[0]).st_mode)[-3:] == "600"
    assert oct(os.stat(app_dir / "exports").st_mode)[-3:] == "700"
    header = exports[0].read_text().splitlines()[0]
    assert "Invoice #" in header and "Status" in header

    detail = next(s for s in at.selectbox if s.label == "Open an invoice")
    for option in detail.options[1:]:
        detail.set_value(option).run()
        _no_errors(at)
        assert at.json
        detail = next(s for s in at.selectbox if s.label == "Open an invoice")

    before = len(db.list_dashboard_rows())
    _button(at, "Delete this invoice record").click().run()
    assert len(db.list_dashboard_rows()) == before  # nothing deleted without confirmation
    assert any("Delete this invoice record and its validation result?" in m for m in _messages(at))
    _button(at, "Cancel").click().run()
    assert len(db.list_dashboard_rows()) == before
    _button(at, "Delete this invoice record").click().run()
    _button(at, "Yes, delete record").click().run()
    _no_errors(at)
    assert len(db.list_dashboard_rows()) == before - 1


def test_empty_app_renders(app_dir):
    for pdf in (app_dir / "invoices").glob("*.pdf"):
        pdf.unlink()
    at = _app(app_dir)
    _no_errors(at)
    assert any("No invoices match" in m for m in _messages(at))
    assert any("No contractors yet" in m.value for m in at.markdown)


def test_contractor_column_can_be_hidden(app_dir):
    db.add_contract_term(db.upsert_contractor("Test Contractor"), 4000.0, "2026-01", None)
    at = _app(app_dir)
    _button(at, "Validate all invoices").click().run()
    results_table = lambda: _results(at)
    assert "Contractor" in results_table().columns  # shown by default

    at.toggle(key="show_contractor").set_value(False).run()
    _no_errors(at)
    assert "Contractor" not in results_table().columns
    assert "Status" in results_table().columns and "Invoice #" in results_table().columns
    _button(at, "Export visible results to CSV").click().run()
    header = next((app_dir / "exports").glob("*.csv")).read_text().splitlines()[0]
    assert "Contractor" not in header.split(",")

    at.toggle(key="show_contractor").set_value(True).run()
    assert "Contractor" in results_table().columns


def test_dashboard_and_lists_sort_by_surname(app_dir, monkeypatch):
    for f in (app_dir / "invoices").glob("*.pdf"):
        f.unlink()
    from reportlab.pdfgen import canvas

    def fake_invoice(filename, number):  # distinct content -> distinct files
        c = canvas.Canvas(str(app_dir / "invoices" / filename))
        for i, line in enumerate([f"Invoice no. {number}", "Invoice date: 31.01.2026", "Total due: $ 1,000.00"]):
            c.drawString(40, 800 - 18 * i, line)
        c.save()

    for i, name in enumerate(["Zoe Abbott", "Adam Walker", "Olivia Carter", "Jack Brooks"]):
        fake_invoice(f"{name}_2026-01.pdf", f"T-{i}")
    fake_invoice("Olivia Carter_2026-02.pdf", "T-9")
    at = _app(app_dir)
    _button(at, "Validate all invoices").click().run()
    table = _results(at)
    assert list(table["Contractor"]) == ["Abbott Zoe", "Brooks Jack", "Carter Olivia", "Carter Olivia", "Walker Adam"]
    assert list(table[table["Contractor"] == "Carter Olivia"]["Service Month"]) == ["2026-02", "2026-01"]

    next(s for s in at.selectbox if s.label == "Sort").set_value("Surname Z–A").run()
    assert list(_results(at)["Contractor"]) == ["Walker Adam", "Carter Olivia", "Carter Olivia", "Brooks Jack", "Abbott Zoe"]

    filter_box = next(s for s in at.selectbox if s.label == "Contractor")
    assert filter_box.options == ["All", "Abbott Zoe", "Brooks Jack", "Carter Olivia", "Walker Adam"]
    assert at.selectbox(key="contracts_selected").options == ["Abbott Zoe", "Brooks Jack", "Carter Olivia", "Walker Adam"]



def test_names_shown_surname_first_but_stored_unchanged(app_dir):
    db.add_contract_term(db.upsert_contractor("Test Contractor"), 4000.0, "2026-01", None)
    at = _app(app_dir)
    _button(at, "Validate all invoices").click().run()
    assert set(_results(at)["Contractor"]) == {"Contractor Test", "—"}  # "—" = unrecognised file
    assert at.selectbox(key="contracts_selected").value == "Test Contractor"  # stored order unchanged
    assert any("Terms for Contractor Test" in m.value for m in at.markdown)
    # Typing the name surname-first still finds the same contractor
    at.text_input(key="new_contractor_name").set_value("Contractor Test")
    _button(at, "Add contractor").click().run()
    assert len(db.list_contractors()) == 1
    assert (app_dir / "invoices" / "Test Contractor_2026-01.pdf").exists()  # files untouched


def test_backups_section_backup_and_confirmed_restore(app_dir):
    from pipeline import backup
    at = _app(app_dir)  # opening the app makes the daily backup
    assert any(b["reason"] == "daily" for b in backup.list_backups())
    db.upsert_contractor("Before Example")
    _button(at, "Back up now").click().run()
    assert any("Backed up" in m for m in _messages(at))
    db.upsert_contractor("After Example")
    at.run()
    at.selectbox(key="restore_choice").set_value(at.selectbox(key="restore_choice").options[0]).run()
    _button(at, "Restore this backup").click().run()
    assert any("Tick the confirmation box" in m for m in _messages(at))
    assert len(db.list_contractors()) == 2  # nothing restored without confirmation
    at.checkbox(key="restore_confirm").check().run()
    _button(at, "Restore this backup").click().run()
    _no_errors(at)
    assert any("Restored" in m for m in _messages(at))
    assert [c["name"] for c in db.list_contractors()] == ["Before Example"]


def _validated_app(app_dir):
    db.add_contract_term(db.upsert_contractor("Test Contractor"), 4000.0, "2026-01", None)
    at = _app(app_dir)
    _button(at, "Validate all invoices").click().run()
    return at


def test_history_tab_overview_table_and_chart(app_dir):
    at = _validated_app(app_dir)
    _no_errors(at)
    grid = next(d.value for d in at.dataframe if "2026-01" in d.value.columns)
    assert list(grid["Contractor"]) == ["Contractor Test"]
    assert grid.loc[0, "2026-01"].endswith("Pass") and grid.loc[0, "2026-02"].endswith("Warning")
    assert grid.loc[0, "Success"] == 17 and grid.loc[0, "Passed"] == "1 of 6"  # 1 Pass in 6 months
    assert list(grid.columns[:3]) == ["Contractor", "Success", "Passed"]
    per_month = next(d.value for d in at.dataframe if "Difference EUR" in d.value.columns)
    assert list(per_month["Month"]) == ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"]
    assert per_month.loc[1, "Difference EUR"] == "-200.00"  # 3,800 invoiced vs 4,000 contract
    assert _charts(at), "difference chart should render"


def test_oncall_tab_imports_sheet_and_validation_uses_the_extra(app_dir):
    from tests.fixtures.make_oncall_xlsx import GREEN, write_attendance_xlsx
    (app_dir / "oncall").mkdir()
    # April: Test Contractor on call for 2 weekdays (5 h standby) and one GREEN holiday (14 h), 2 h active work.
    write_attendance_xlsx(app_dir / "oncall" / "attendance.xlsx", {"Apr": {"rows": [
        (None, ["01.04.2026", "Staff Person", "Contractor Test", 5, "2h", "", "", ""]),
        (None, ["02.04.2026", "Staff Person", "Contractor Test", 5, "", "", "", ""]),
        (GREEN, ["03.04.2026", "Staff Person", "Contractor Test", 14, "", "", "", ""]),
    ]}})
    at = _validated_app(app_dir)
    assert {r["original_filename"]: r["status"] for r in db.list_dashboard_rows()}["Test Contractor_2026-04.pdf"] == "Manual Review"

    assert at.multiselect(key="oncall_months").value == ["2026-04"]  # the latest month is preselected
    _button(at, "Import on-call hours").click().run()
    _no_errors(at)
    assert any("Imported 1 contractor-month(s)" in m and "re-validated 1 invoice(s) of 2026-04" in m
               for m in _messages(at))
    # The import re-validated April on its own: no need to press Validate again.
    april_now = next(r for r in db.list_dashboard_rows() if r["original_filename"] == "Test Contractor_2026-04.pdf")
    assert april_now["oncall_extra_eur"] == 145.0 and april_now["status"] == "Warning"
    dash = _results(at)
    assert dash.loc[dash["Service Month"] == "2026-04", "On-call EUR"].iloc[0] == "145.00"
    assert dash.loc[dash["Service Month"] == "2026-01", "On-call EUR"].iloc[0] == "—"
    assert any("Skipped as staff" in m and "Staff Person" in m for m in _messages(at))
    hours = next(d.value for d in at.dataframe if "Extra EUR" in d.value.columns)
    row = hours.to_dict("records")[0]
    assert row["Contractor"] == "Contractor Test" and row["Month"] == "2026-04"
    assert (row["Standby"], row["Standby (holiday)"], row["Active evening"]) == (10.0, 14.0, 2.0)
    # contract 4,000: hourly 25 -> 10*25*0.10 + 14*25*0.15 + 2*25*1.35 = 25 + 52.50 + 67.50
    assert row["Hourly EUR"] == "25.00" and row["Extra EUR"] == "145.00"
    assert row["Extra USD"] == f"{round(145.0 * FAKE_FX['rate'], 2):,.2f}" and row["ECB Rate"] == f"{FAKE_FX['rate']:.4f}"

    _button(at, "Validate all invoices").click().run()
    april = next(r for r in db.list_dashboard_rows() if r["original_filename"] == "Test Contractor_2026-04.pdf")
    # The fixture's On Call line is 400, not 145 -> the EUR total is off and stays a Warning
    assert april["status"] == "Warning" and april["oncall_extra_eur"] == 145.0 and april["expected_eur"] == 4145.0
    assert any("plus the on-call extra of 145.00 EUR" in w for w in april["warnings"])
    assert any("On Call" in w and "no on-call hours" in w for w in april["warnings"]) is False


def test_report_tab_counts_attempts_and_new_versions(app_dir):
    at = _validated_app(app_dir)
    _no_errors(at)
    # A corrected version of the February invoice arrives: same file name,
    # different content (a new attempt). Everything else is validated again unchanged.
    feb = app_dir / "invoices" / "Test Contractor_2026-02.pdf"
    feb.write_bytes(feb.read_bytes() + b"\n%% corrected version\n")
    _button(at, "Validate all invoices").click().run()
    _no_errors(at)

    per_contractor = next(d.value for d in at.dataframe if "Most attempts" in d.value.columns)
    assert per_contractor.to_dict("records") == [{
        "Contractor": "Contractor Test", "Success": 17, "Right": "1 of 6", "Right first time": "1 of 6",
        "Attempts": 7, "New versions": 1, "Most attempts": 2,
    }]
    per_invoice = next(d.value for d in at.dataframe if "Final result" in d.value.columns)
    assert list(per_invoice["Month"]) == ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"]
    assert list(per_invoice["Attempts"]) == [1, 2, 1, 1, 1, 1]
    assert list(per_invoice["New versions"]) == [0, 1, 0, 0, 0, 0]
    assert list(per_invoice["Right"]) == ["✅", "❌", "❌", "❌", "❌", "❌"]
    assert per_invoice.loc[1, "First result"].endswith("Warning") and per_invoice.loc[0, "Final result"].endswith("Pass")
    tiles = next(m.value for m in at.markdown if "New versions uploaded" in m.value)
    assert "1 of 6" in tiles and "Attempts" in tiles


def test_privacy_mode_hides_names_amounts_and_editing(app_dir):
    at = _validated_app(app_dir)
    at.toggle(key="privacy_mode").set_value(True).run()
    _no_errors(at)
    every_cell = " ".join(str(v) for d in at.dataframe for v in d.value.astype(str).values.ravel())
    assert "Test Contractor" not in every_cell and "Contractor Test" not in every_cell
    assert "Contractor A" in every_cell
    assert "4,000.00" not in every_cell and "4,340.00" not in every_cell
    assert "••" in every_cell
    assert not _charts(at), "chart is hidden in privacy mode"
    assert any("Privacy mode is on" in m for m in _messages(at))
    assert not [b for b in at.button if b.label.startswith("Save term for")]  # editing paused
    assert any("privacy mode" in m.value for m in at.markdown)  # header badge
    detail = next(s for s in at.selectbox if s.label == "Open an invoice")
    assert all("Test Contractor" not in o and ".pdf" not in o for o in detail.options)
    for option in [o for o in detail.options if o.startswith("Contractor A")]:
        detail.set_value(option).run()
        import json
        shown = json.loads(at.json[0].value)
        flat = json.dumps(shown, ensure_ascii=False)
        assert "Test Contractor" not in flat and shown["contractor"] == "Contractor A"
        assert "4000.0" not in flat and "4340.0" not in flat
        assert ui_privacy_mask() in flat
        detail = next(s for s in at.selectbox if s.label == "Open an invoice")

    at.toggle(key="privacy_mode").set_value(False).run()
    assert "Contractor Test" in " ".join(_results(at)["Contractor"].astype(str))


def ui_privacy_mask():
    import ui_privacy
    return ui_privacy.MASK



def _real_login(app_dir):
    auth.set_password("secret-1")
    at = _app(app_dir, logged_in=False)
    at.text_input[0].set_value("secret-1")
    at.button[0].click().run()
    return at, _issued_token(at)


def test_session_script_keeps_cookie_and_idle_logout_when_logged_in(app_dir):
    at, token = _real_login(app_dir)
    scripts = [h.value for h in at.get("html")]
    idle = next(v for v in scripts if "IDLE_MS" in v)
    assert f"IDLE_MS = {auth.IDLE_SECONDS * 1000}" in idle and auth.IDLE_SECONDS == 30
    assert f'document.cookie = "{auth.COOKIE_NAME}=" + "{token}"' in idle
    assert "SameSite=Strict" in idle
    assert 'searchParams.set("logout", why)' in idle
    assert abs(int(idle.split("EXPIRES_MS = ")[1].split(";")[0]) / 1000 - auth.session_expires_at(token)) < 1
    assert not at.query_params


def test_logged_out_page_clears_the_cookie(app_dir):
    auth.set_password("secret-1")
    at = _app(app_dir, logged_in=False)
    scripts = [h.value for h in at.get("html")]
    assert any(f"{auth.COOKIE_NAME}=; path=/; SameSite=Strict; max-age=0" in v for v in scripts)
    assert not any("IDLE_MS" in v for v in scripts)


def test_idle_logout_ends_the_session(app_dir):
    _, token = _real_login(app_dir)
    at = _app(app_dir, logged_in=False, token=token, logout="idle")  # what the page script does
    assert _title(at)[0].endswith("Login")
    assert any("30 seconds without activity" in m for m in _messages(at))
    assert not auth.session_is_valid(token)
    assert _title(_app(app_dir, logged_in=False, token=token))[0].endswith("Login")  # cookie no longer works


def test_login_expires_after_15_minutes_even_mid_session(app_dir, monkeypatch):
    import time as _time
    at, token = _real_login(app_dir)
    assert TITLE in _title(at)
    start = _time.time()
    monkeypatch.setattr(_time, "time", lambda: start + 14 * 60)
    at.run()
    assert TITLE in _title(at)  # still logged in at 14 minutes
    monkeypatch.setattr(_time, "time", lambda: start + 15 * 60 + 5)
    at.run()
    assert _title(at)[0].endswith("Login")
    assert any("15-minute login ended" in m for m in _messages(at))
