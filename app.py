"""Streamlit dashboard — binds to 127.0.0.1 only (see .streamlit/config.toml),
so it is never reachable from off this machine. Logging below intentionally
includes only invoice ids / status codes, never field values."""

import csv
import json
import os
import time
import unicodedata
import datetime as dt
import logging
from pathlib import Path

import pandas as pd
import streamlit as st

import auth
import ui_privacy
import ui_style
from pipeline import backup, contracts, db, fx, history, naming, oncall, process, report, validate

from ui_style import STATUS_STYLE  # noqa: E402  (icon + label + tint per status)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("invoice-validator")

PROJECT_ROOT = Path(__file__).resolve().parent
INVOICES_DIR = PROJECT_ROOT / "invoices"


def _invoice_pdfs():
    """Every PDF under invoices/, including month subfolders such as
    'September 26/'. The folder is just organisation — the contractor and
    month still come from the file name."""
    return sorted(p for p in INVOICES_DIR.rglob("*.pdf") if not p.name.startswith("."))
EXPORTS_DIR = PROJECT_ROOT / "exports"
ONCALL_DIR = PROJECT_ROOT / "oncall"

st.set_page_config(page_title="Invoice Validator", page_icon="👑", layout="wide")
ui_style.apply_theme(st)
db.init_db()
try:
    backup.auto_backup_if_due()  # one automatic snapshot per day, kept in data/backups/
except Exception as e:  # a failed backup must never stop the app from opening
    logging.getLogger("invoice-validator").warning("automatic backup failed: %s", type(e).__name__)


def _session_script(token: str, expires_at: float) -> str:
    """Runs in the page while logged in. Keeps the session token in a cookie
    (so a refresh or new tab stays logged in without the token ever being in
    the URL), logs out after auth.IDLE_SECONDS without mouse, keyboard,
    scroll or touch activity, and when the login's absolute expiry is
    reached. Logging out reloads the page as ?logout=<why>; the server then
    ends the session named by the cookie."""
    max_age = max(int(expires_at - time.time()), 1)
    return f"""<script>
(function () {{
  const IDLE_MS = {auth.IDLE_SECONDS * 1000};
  const EXPIRES_MS = {int(expires_at * 1000)};
  const w = window;
  w.document.cookie = "{auth.COOKIE_NAME}=" + {json.dumps(token)} + "; path=/; SameSite=Strict; max-age={max_age}";
  if (w.__invIdle) {{ w.__invIdle.update(EXPIRES_MS); return; }}
  let last = Date.now(), expires = EXPIRES_MS, stopped = false;
  const reset = () => {{ last = Date.now(); }};
  ["mousemove","mousedown","keydown","scroll","wheel","touchstart","pointerdown"].forEach(
    ev => w.document.addEventListener(ev, reset, {{passive: true, capture: true}}));
  const leave = (why) => {{
    if (stopped) return; stopped = true;
    const u = new URL(w.location.href);
    u.search = ""; u.searchParams.set("logout", why);
    w.location.replace(u.toString());
  }};
  const timer = setInterval(() => {{
    if (stopped) {{ clearInterval(timer); return; }}
    if (Date.now() - last > IDLE_MS) leave("idle");
    else if (Date.now() > expires) leave("expired");
  }}, 1000);
  w.__invIdle = {{ update: (e) => {{ expires = e; reset(); }}, stop: () => {{ stopped = true; }} }};
}})();
</script>"""


_LOGGED_OUT_SCRIPT = f"""<script>
window.__invIdle && window.__invIdle.stop();
document.cookie = "{auth.COOKIE_NAME}=; path=/; SameSite=Strict; max-age=0";
</script>"""


def _logged_out(reason=None):
    st.session_state["authenticated"] = False
    st.session_state.pop("auth_token", None)
    if reason:
        st.session_state["logout_reason"] = reason


def _browser_token():
    """The session token this websocket session was opened with (cookie),
    or the one issued in this very session right after logging in."""
    return st.session_state.get("auth_token") or auth.token_from_cookies(st.context.cookies)


def require_auth() -> bool:
    # An automatic logout from the page script arrives as ?logout=idle|expired.
    # (The token itself never travels in the URL; the cookie names the session.)
    if st.query_params.get("logout"):
        auth.end_session(_browser_token())
        why = st.query_params.get("logout")
        st.query_params.clear()
        _logged_out({"idle": f"You were logged out after {auth.IDLE_SECONDS} seconds without activity 💤",
                     "expired": f"Your {auth.SESSION_MINUTES}-minute login ended. Log in again 🔐"}.get(why))

    token = _browser_token()
    if token and auth.session_is_valid(token):
        st.session_state["authenticated"] = True
        st.session_state["auth_token"] = token
        st.html(_session_script(token, auth.session_expires_at(token)), unsafe_allow_javascript=True)
        return True
    if st.session_state.get("authenticated"):
        _logged_out(f"Your {auth.SESSION_MINUTES}-minute login ended. Log in again 🔐")
    st.html(_LOGGED_OUT_SCRIPT, unsafe_allow_javascript=True)

    ui_style.hero(st, "🔒 for my eyes only")
    st.title("Invoice Validator · Login")
    ui_style.tagline(st, "hi gorgeous, password please 🔐")
    reason = st.session_state.pop("logout_reason", None)
    if reason:
        st.info(reason)
    if not auth.is_configured():
        st.info("No app password set yet. Choose one now — it's stored only on this machine.")
        with st.form("set_password"):
            pw1 = st.text_input("New password", type="password")
            pw2 = st.text_input("Confirm password", type="password")
            submitted = st.form_submit_button("Set password", type="primary")
        if submitted:
            if not pw1 or pw1 != pw2:
                st.error("Passwords are empty or don't match.")
            else:
                auth.set_password(pw1)
                st.session_state.update(authenticated=True, auth_token=auth.create_session())
                st.rerun()
    else:
        with st.form("login"):
            pw = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Log in", type="primary")
        if submitted:
            if auth.verify_password(pw):
                st.session_state.update(authenticated=True, auth_token=auth.create_session())
                st.rerun()
            else:
                st.error("Incorrect password.")
    return False


if not require_auth():
    st.stop()

title_col, logout_col = st.columns([5, 1], vertical_alignment="top")
with logout_col.container(key="header_right", horizontal_alignment="right"):  # Log out top-right, Privacy under it
    if st.button("Log out"):
        auth.end_session(_browser_token())
        _logged_out()
        st.rerun()
    privacy_on = st.toggle("🙈 Privacy", key="privacy_mode",
                           help="Hide contractor names, amounts and invoice text, e.g. while sharing your screen.")
with title_col:
    st.title("Invoice Validator")
    if privacy_on:
        ui_style.tagline(st, "privacy mode · names and amounts hidden 🙈")
    else:
        ui_style.tagline(st, "checking contractor invoices so you don't have to 💅")
view = ui_privacy.View(privacy_on, [c["name"] for c in db.list_contractors()])
tab_validate, tab_dashboard, tab_history, tab_report, tab_oncall, tab_contracts = st.tabs(
    ["✨ Validate", "📊 Dashboard", "🗓️ History", "📋 Report", "📟 On-call", "📑 Contracts"]
)

with tab_validate:
    unnamed = [p for p in _invoice_pdfs() if not naming.follows_convention(p)]
    if unnamed:
        st.subheader("Name files so contractors are recognised 🏷️")
        if view.private:
            st.info(f"🙈 {len(unnamed)} file(s) need naming. Turn off privacy mode to see and rename them.")
        else:
            st.write(
                f"{len(unnamed)} file(s) don't follow the `Contractor_YYYY-MM.pdf` pattern, so the app "
                "can't tell whose invoice it is or which month it covers. Check the suggested contractor "
                "and month (the month the work was done), tick **Rename**, then click the button. "
                "Suggestions come from the filename only; blank means it couldn't be guessed safely."
            )
            known = [c["name"] for c in db.list_contractors()]
            if known:
                st.caption("Contractors already set up: " + ", ".join(sorted((naming.display_name(k) for k in known), key=str.casefold)) + ". Use the exact same spelling.")
            edited = st.data_editor(
                pd.DataFrame(
                    [
                        {
                            "File": str(p.relative_to(INVOICES_DIR)),
                            "Contractor": naming.suggest_contractor(p.stem, known),
                            "Month (YYYY-MM)": naming.suggest_month(p.stem),
                            "Rename": False,
                        }
                        for p in unnamed
                    ]
                ),
                disabled=["File"],
                hide_index=True,
                width="stretch",
                key="rename_editor",
            )
            if st.button("Rename ticked files"):
                renamed, problems = 0, []
                for row in edited.to_dict("records"):
                    if not row["Rename"]:
                        continue
                    try:
                        naming.rename_to_convention(INVOICES_DIR / row["File"], row["Contractor"], row["Month (YYYY-MM)"])
                        renamed += 1
                    except (ValueError, FileExistsError, FileNotFoundError) as e:
                        problems.append(f"{row['File']}: {e}")
                st.session_state["rename_report"] = (renamed, problems)
                st.session_state.pop("rename_editor", None)
                st.rerun()
    rename_outcome = st.session_state.pop("rename_report", None)
    if rename_outcome:
        renamed, problems = rename_outcome
        if renamed:
            st.success(f"Renamed {renamed} file(s). Run validation below to update their results.")
        for msg in problems:
            st.error(f"Not renamed — {msg}")
        if not renamed and not problems:
            st.info("No rows were ticked, so nothing was renamed.")
    new_names = sorted(
        {naming.parse_filename(p)[0] for p in _invoice_pdfs() if naming.follows_convention(p)}
        - {c["name"] for c in db.list_contractors()}
    )
    if new_names:
        who_new = f"{len(new_names)} contractor(s)" if view.private else ", ".join(naming.display_name(n) for n in new_names)
        st.info(
            f"Not yet in the Contracts tab: {who_new}. They'll be added automatically when you validate, "
            "but need a contract term before Rule A can be checked."
        )

    st.subheader("Check my invoices ✨")
    all_pdfs = _invoice_pdfs()
    folders = sorted({str(p.parent.relative_to(INVOICES_DIR)) for p in all_pdfs} - {"."})
    st.write(f"📂 {len(all_pdfs)} PDF(s) waiting in `invoices/`" + (f" ({', '.join(folders)})." if folders else "."))
    scope = "All folders"
    if folders:
        scope = st.selectbox("Which folder", ["All folders", *folders], index=len(folders),
                             help="Validate only the PDFs in one month folder, or everything.", key="validate_folder")
    pdfs = all_pdfs if scope == "All folders" else [p for p in all_pdfs if str(p.parent.relative_to(INVOICES_DIR)) == scope]
    label = "Validate all invoices" if scope == "All folders" else f"Validate {scope}"
    if st.button(label, type="primary", disabled=not pdfs):
        progress = st.progress(0.0)
        results = []
        for i, pdf_path in enumerate(pdfs):
            try:
                result = process.process_invoice_file(pdf_path)
                logger.info("processed invoice id=%s status=%s", result.get("invoice_id"), result["status"])
                results.append((pdf_path.name, result["status"]))
            except Exception as e:
                logger.exception("invoice processing failed: %s", type(e).__name__)
                results.append((pdf_path.name, f"ERROR: {type(e).__name__}"))
            progress.progress((i + 1) / max(len(pdfs), 1))
        st.success(f"Processed {len(pdfs)} invoice(s). All done, queen 👑")
        for name, status in results:
            icon = STATUS_STYLE.get(status, {}).get("icon", "💥")
            if view.private:
                who, year, month = naming.parse_filename(Path(name))
                name = f"{view.who(who)} {year}-{month:02d}" if who else "unrecognised file"
            st.write(f"{icon} {name}: **{status}**")

    with st.expander("🛟 Backups"):
        def _backup_now():
            path = backup.create_backup("manual")
            st.session_state["backup_msg"] = ("success", f"Backed up 💾 ({path.name})")

        def _restore(path_str):
            if not st.session_state.get("restore_confirm"):
                st.session_state["backup_msg"] = ("error", "Tick the confirmation box first.")
                return
            safety = backup.restore_backup(Path(path_str))
            st.session_state["restore_confirm"] = False
            st.session_state["backup_msg"] = (
                "success",
                f"Restored. Your data from just before the restore was saved as {safety.name}, "
                "so this can be undone the same way.",
            )

        st.caption(
            "A backup is made automatically once a day when the app opens. The newest "
            f"{backup.KEEP} are kept in `data/backups/` on this computer only."
        )
        st.button("Back up now", on_click=_backup_now)
        backup_msg = st.session_state.pop("backup_msg", None)
        if backup_msg:
            getattr(st, backup_msg[0])(backup_msg[1])
        backups = backup.list_backups()
        if backups:
            labels = {
                f"{b['created']:%d.%m.%Y %H:%M:%S} · {b['reason'].replace('-', ' ')}": str(b["path"]) for b in backups
            }
            chosen = st.selectbox("Restore from", list(labels), key="restore_choice")
            st.checkbox(
                "I understand this replaces the current contractors, contracts and results", key="restore_confirm"
            )
            st.button("Restore this backup", on_click=_restore, args=(labels[chosen],))
        else:
            st.write("No backups yet.")

with tab_dashboard:
    st.subheader("How everyone's doing 💅")
    all_rows = db.list_dashboard_rows()
    st.session_state.setdefault("dash_status", "All")
    clicked_tile = ui_style.stat_buttons(
        st, len(all_rows), {label: sum(1 for r in all_rows if r["status"] == label) for label in STATUS_STYLE},
        selected=st.session_state["dash_status"],
    )
    if clicked_tile:
        st.session_state["dash_status"] = clicked_tile
        st.rerun()
    contractors = sorted((c["name"] for c in db.list_contractors()), key=naming.surname_sort_key)
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        f_contractor = st.selectbox(
            "Contractor", ["All"] + contractors, format_func=lambda n: n if n == "All" else view.who(n)
        )
    with col2:
        f_month = st.text_input("Service month (YYYY-MM)", "")
    with col3:
        f_status = st.selectbox("Status", ["All", "Pass", "Warning", "Manual Review", "Unable to Verify"], key="dash_status")
    with col4:
        f_sort = st.selectbox("Sort", ["Surname A–Z", "Surname Z–A", "Newest first", "Oldest first"])

    rows = db.list_dashboard_rows(
        contractor=None if f_contractor == "All" else f_contractor,
        service_month=f_month.strip() or None,
        status=None if f_status == "All" else f_status,
        newest_first=(f_sort != "Oldest first"),
    )
    if f_sort.startswith("Surname"):
        # Rows arrive newest-first; a stable sort by surname keeps each
        # person's invoices newest-first within their group.
        rows = sorted(rows, key=lambda r: naming.surname_sort_key(r["contractor_name"] or ""),
                      reverse=(f_sort == "Surname Z–A"))

    if not rows:
        st.info("No invoices match these filters yet. Run Validate first.")
    else:
        table_data = [
            {
                "Status": f"{STATUS_STYLE.get(r['status'], {}).get('icon', '')} {r['status']}",
                "Contractor": naming.display_name(r["contractor_name"]) if r["contractor_name"] else None,
                "_contractor_raw": r["contractor_name"],
                "Invoice #": r["invoice_number"],
                "Service Month": r["service_month"],
                "Issue Date": r["issue_date"],
                "Taxable Supply": r["taxable_supply_date"],
                "Due the contract": f"{int(r['due_days'])} days" if r["due_days"] else None,
                "Expected EUR": r["expected_eur"],
                "On-call EUR": r["oncall_extra_eur"],
                "Expected USD": r["expected_usd"],
                "Invoiced USD": r["invoiced_usd"],
                "ECB Rate": r["fx_rate"],
                "Rate Date": r["fx_effective_date"],
                "Warnings": "; ".join(r["warnings"]) if r["warnings"] else "",
            }
            for r in rows
        ]
        def _style_status_cell(cell_text):
            # Only the Status cell gets colored — forcing both background AND
            # a dark text color keeps it legible regardless of whether the
            # viewer is in Streamlit's light or dark theme (a background-only
            # tint goes nearly unreadable against dark-theme white text).
            for label, s in STATUS_STYLE.items():
                if label in cell_text:
                    return f"background-color: {s['bg']}; color: {s['fg']}; font-weight: 700;"
            return ""

        chips_col, toggle_col = st.columns([4, 1], vertical_alignment="center")
        with chips_col:
            ui_style.status_chips(st)
        with toggle_col:
            show_contractor = st.toggle("Show contractor column", value=True, key="show_contractor")
        for row in table_data:
            raw = row.pop("_contractor_raw")
            if view.private:
                row["Contractor"] = view.who(raw) if raw else None
        if not show_contractor:
            # Hidden from the table and from "Export visible results"; the
            # Contractor filter above and the detail view below still use it.
            table_data = [{k: v for k, v in row.items() if k != "Contractor"} for row in table_data]
        # On-screen copy: numbers formatted and empty cells shown as a dash
        # (Streamlit draws real nulls as "None"). The CSV export keeps the raw values.
        money = {"Expected EUR": "{:,.2f}", "On-call EUR": "{:,.2f}", "Expected USD": "{:,.2f}",
                 "Invoiced USD": "{:,.2f}", "ECB Rate": "{:.4f}"}
        def _cell(k, v):
            if v is None or v == "":
                return "—"
            if k == "ECB Rate":  # public data
                return money[k].format(v)
            if k in money:
                return view.amount(v, money[k])
            if k in ("Invoice #", "Warnings"):
                return view.text(v)
            return v

        display_rows = [{k: _cell(k, v) for k, v in row.items()} for row in table_data]
        styled_df = pd.DataFrame(display_rows).style.map(_style_status_cell, subset=["Status"])
        st.dataframe(styled_df, width="stretch", height="content", hide_index=True)

        st.markdown("#### Invoice detail 🔍")
        options = {
            (f"{view.who(r['contractor_name'])} — {r['service_month']} — record {r['id']}" if view.private
             else f"{naming.display_name(r['contractor_name'] or '')} — {r['service_month']} — {r['original_filename']}"): r
            for r in rows
        }
        choice = st.selectbox("Open an invoice", ["—"] + list(options.keys()))
        if choice != "—":
            r = options[choice]
            st.json(
                {
                    "status": r["status"],
                    "warnings": [view.text(w) for w in r["warnings"]],
                    "contractor": view.who(r["contractor_name"]),
                    "invoice_number": view.text(r["invoice_number"]),
                    "service_month": r["service_month"],
                    "service_month_source": r["service_month_source"],
                    "issue_date": r["issue_date"],
                    "issue_date_snippet": view.snippet(r["issue_date_snippet"]),
                    "due_date": r["due_date"],
                    "payment_date": r["payment_date"],
                    "taxable_supply_date": r["taxable_supply_date"],
                    "taxable_supply_snippet": view.snippet(r["taxable_supply_snippet"]),
                    "standard_amount_value": view.snippet(r["standard_amount_value"]),
                    "standard_amount_snippet": view.snippet(r["standard_amount_snippet"]),
                    "expected_eur": view.snippet(r["expected_eur"]),
                    "usd_amount_value": view.snippet(r["usd_amount_value"]),
                    "usd_amount_snippet": view.snippet(r["usd_amount_snippet"]),
                    "expected_usd": view.snippet(r["expected_usd"]),
                    "fx_rate": r["fx_rate"],
                    "fx_effective_date": r["fx_effective_date"],
                    "fx_source": r["fx_source"],
                    "oncall_present": bool(r["oncall_present"]),
                    "oncall_extra_eur": view.snippet(r["oncall_extra_eur"]),
                    "invoiced_eur_compared": view.snippet(r["invoiced_eur"]),
                    "oncall_snippet": view.snippet(r["oncall_snippet"]),
                    "extraction_method": r["extraction_method"],
                }
            )
            if st.session_state.get("confirm_delete_invoice") == r["id"]:
                st.warning(
                    "Delete this invoice record and its validation result? The PDF stays in `invoices/` "
                    "and comes back if you validate again. A daily backup can restore it."
                )
                y, n, _ = st.columns([1, 1, 4])
                if y.button("Yes, delete record", type="primary", key=f"yes_delete_{r['id']}"):
                    backup.create_backup("before-delete")
                    db.delete_invoice(r["id"])
                    st.session_state.pop("confirm_delete_invoice", None)
                    st.rerun()
                if n.button("Cancel", key=f"cancel_delete_{r['id']}"):
                    st.session_state.pop("confirm_delete_invoice", None)
                    st.rerun()
            elif st.button("Delete this invoice record", key=f"delete_{r['id']}"):
                st.session_state["confirm_delete_invoice"] = r["id"]
                st.rerun()

        if st.button("Export visible results to CSV"):
            EXPORTS_DIR.mkdir(exist_ok=True)
            os.chmod(EXPORTS_DIR, 0o700)  # exports hold confidential data: owner-only, like data/
            out_path = EXPORTS_DIR / f"export_{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
            with open(out_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=list(table_data[0].keys()))
                writer.writeheader()
                writer.writerows(table_data)
            os.chmod(out_path, 0o600)
            st.success(f"Exported to {out_path}")

with tab_history:
    st.subheader("Month by month 🗓️")
    hist_rows = db.list_dashboard_rows()
    h_contractors, h_months, h_cells = history.overview(hist_rows)
    if not h_contractors:
        st.info("No validated invoices yet. Run Validate first.")
    else:
        st.caption("Everyone at a glance: one row per contractor, one column per invoiced month.")
        ui_style.status_chips(st)
        def _success_row(c):
            passed, total, pct = history.success(h_cells, c)
            return {"Success": pct, "Passed": f"{passed} of {total}"}

        grid = pd.DataFrame(
            [
                {"Contractor": view.who(c), **_success_row(c),
                 **{m: (f"{STATUS_STYLE[h_cells[(c, m)]]['icon']} {h_cells[(c, m)]}" if (c, m) in h_cells else "")
                    for m in h_months}}
                for c in h_contractors
            ]
        )

        def _tint(cell):
            for label, st_ in STATUS_STYLE.items():
                if isinstance(cell, str) and cell.endswith(label):
                    return f"background-color: {st_['bg']}; color: {st_['fg']}; font-weight: 650;"
            return ""

        st.dataframe(
            grid.style.map(_tint, subset=h_months),
            width="stretch",
            height="content",  # the whole table, no inner scrolling
            hide_index=True,
            column_config={
                "Success": st.column_config.ProgressColumn(
                    "Success", help="Share of this contractor's invoices that passed, across all months",
                    format="%d%%", min_value=0, max_value=100,
                ),
                "Passed": st.column_config.TextColumn("Passed", width="small"),
            },
        )

        st.markdown("#### One contractor 🔎")
        # The key changes with privacy mode: Streamlit keeps a keyed selectbox's displayed label across
        # reruns, so without this the real name would stay visible after switching privacy on.
        who_h = st.selectbox("Contractor", h_contractors, format_func=view.who, key=f"history_contractor_{view.private}")
        entries = history.contractor_history(hist_rows, who_h)
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Month": e["month"],
                        "Status": f"{STATUS_STYLE.get(e['status'], {}).get('icon', '')} {e['status']}",
                        "Invoice #": view.text(e["invoice_number"]),
                        "Invoiced EUR": view.amount(e["invoiced_eur"]),
                        "Expected EUR": view.amount(e["expected_eur"]),
                        "Difference EUR": view.amount(e["difference_eur"], "{:+,.2f}"),
                        "Invoiced USD": view.amount(e["invoiced_usd"]),
                        "Expected USD": view.amount(e["expected_usd"]),
                        "Date checked": e["checked_date"] or "—",
                    }
                    for e in entries
                ]
            ).style.map(_tint, subset=["Status"]),
            width="stretch",
            height="content",  # the whole table, no inner scrolling
            hide_index=True,
        )

        chart_points = [e for e in entries if e["difference_eur"] is not None]
        if view.private:
            st.caption("🙈 The difference chart is hidden in privacy mode.")
        elif chart_points and all(e["difference_eur"] == 0 for e in chart_points):
            st.caption("♥️ Every invoice here matches the contract amount exactly, so there's no difference to chart.")
        elif chart_points:
            import altair as alt

            limit = validate.ACCEPTABLE_DIFFERENCE_EUR
            data = pd.DataFrame(
                [
                    {
                        "Month": e["month"],
                        "Difference (EUR)": e["difference_eur"],
                        "Compared with contract": "Above contract" if e["difference_eur"] > 0 else "Below or equal",
                        "Status": e["status"],
                    }
                    for e in chart_points
                ]
            )
            months_order = [e["month"] for e in chart_points]
            span = max(limit * 1.6, max(abs(v) for v in data["Difference (EUR)"]) * 1.15)
            x = alt.X("Month:O", sort=months_order, title=None, axis=alt.Axis(labelAngle=0, labelColor="#4a1d3a"))
            band = alt.Chart(pd.DataFrame({"y": [-limit], "y2": [0]})).mark_rect(
                color="#cfe0f7", opacity=0.55
            ).encode(y="y:Q", y2="y2:Q")
            band_label = alt.Chart(pd.DataFrame({"y": [-limit / 2], "t": [f"accepted underpayment (≤ {limit:,.0f} EUR)"]})).mark_text(
                align="left", dx=6, color="#52514e", fontSize=11
            ).encode(y="y:Q", text="t:N", x=alt.value(0))
            bars = alt.Chart(data).mark_bar(cornerRadiusEnd=4, size=26).encode(
                x=x,
                y=alt.Y("Difference (EUR):Q", scale=alt.Scale(domain=[-span, span]),
                        title="Invoice minus contract (EUR)", axis=alt.Axis(grid=True, gridColor="#f3d6e6", labelColor="#4a1d3a")),
                color=alt.Color("Compared with contract:N",
                                scale=alt.Scale(domain=["Below or equal", "Above contract"], range=["#2a78d6", "#e34948"]),
                                legend=alt.Legend(orient="top", title=None, labelColor="#4a1d3a")),
                tooltip=[alt.Tooltip("Month:O"), alt.Tooltip("Difference (EUR):Q", format="+,.2f"), alt.Tooltip("Status:N")],
            )
            zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color="#9d4e7a", strokeWidth=1).encode(y="y:Q")
            chart = (band + band_label + bars + zero).properties(height=280, background="transparent").configure_view(stroke=None)
            st.altair_chart(chart, width="stretch")
        else:
            st.caption("No amount differences to chart for this contractor yet.")

with tab_report:
    st.subheader("Report card 📋")
    rep_invoices = report.invoice_report(db.list_dashboard_rows())
    if not rep_invoices:
        st.info("No validated invoices yet. Run Validate first.")
    else:
        st.caption(
            "One invoice = one contractor for one month. Every different file you validated for it is an "
            "attempt; new versions are the corrected files uploaded after the first one. "
            "An invoice is right when its latest version passed ♥️"
        )
        t = report.totals(rep_invoices)
        ui_style.kpi_tiles(st, [
            ("Contractors", "🧑‍💻", t["contractors"]),
            ("Invoices right", "♥️", f"{t['right']} of {t['invoices']}"),
            ("Right first time", "🎯", f"{t['right_first_time']} of {t['invoices']}"),
            ("Attempts", "🔁", t["attempts"]),
            ("New versions uploaded", "📤", t["new_versions"]),
        ])

        st.markdown("#### Per contractor 🧑‍💻")
        rep_contractors = report.contractor_report(rep_invoices)
        st.dataframe(
            pd.DataFrame([
                {
                    "Contractor": view.who(c["contractor"]),
                    "Success": c["success_pct"],
                    "Right": f"{c['right']} of {c['invoices']}",
                    "Right first time": f"{c['right_first_time']} of {c['invoices']}",
                    "Attempts": c["attempts"],
                    "New versions": c["new_versions"],
                    "Most attempts": c["most_attempts"],
                }
                for c in rep_contractors
            ]),
            width="stretch",
            height="content",  # the whole table, no inner scrolling
            hide_index=True,
            column_config={
                "Success": st.column_config.ProgressColumn(
                    "Success", help="Share of this contractor's invoices whose latest version passed",
                    format="%d%%", min_value=0, max_value=100,
                ),
                "Attempts": st.column_config.NumberColumn(help="Different invoice files validated, over all months"),
                "New versions": st.column_config.NumberColumn(help="Corrected files uploaded after the first one"),
                "Most attempts": st.column_config.NumberColumn(help="The single month that needed the most versions"),
            },
        )

        st.markdown("#### Per invoice 🧾")
        rep_names = [c["contractor"] for c in rep_contractors]
        who_r = st.selectbox("Show", ["Everyone", *rep_names], key=f"report_contractor_{view.private}",
                             format_func=lambda n: n if n == "Everyone" else view.who(n))
        shown = [i for i in rep_invoices if who_r == "Everyone" or i["contractor"] == who_r]

        def _result(status):
            icon = STATUS_STYLE.get(status, {}).get("icon", "⏳")
            return f"{icon} {status}"

        st.dataframe(
            pd.DataFrame([
                {
                    "Contractor": view.who(i["contractor"]),
                    "Month": i["month"],
                    "Attempts": i["attempts"],
                    "New versions": i["new_versions"],
                    "First result": _result(i["first_status"]),
                    "Final result": _result(i["final_status"]),
                    "Right": "✅" if i["right"] else "❌",
                }
                for i in shown
            ]),
            width="stretch",
            height="content",  # the whole table, no inner scrolling
            hide_index=True,
        )

with tab_oncall:
    st.subheader("On-call extra 📟")
    st.caption(
        f"Hourly rate = contract EUR ÷ {oncall.WORKING_DAYS_PER_MONTH} days ÷ {oncall.HOURS_PER_DAY} hours. "
        "Each kind of hour × hourly rate × its multiplier, added up. Validation then expects contract + extra."
    )

    with st.expander("⚙️ Multipliers"):
        rates = oncall.get_rates()
        with st.form("oncall_rates"):
            new_rates = {}
            cols = st.columns(len(oncall.KINDS))
            for col, (key, label, _) in zip(cols, oncall.KINDS):
                new_rates[key] = col.number_input(label, min_value=0.0, value=float(rates[key]), step=0.05,
                                                  format="%.2f", key=f"rate_{key}")
            if st.form_submit_button("Save multipliers"):
                oncall.set_rates(new_rates)
                st.session_state["oncall_msg"] = ("success", "Multipliers saved ✨")
                st.rerun()

    st.markdown("#### Import the attendance sheet 📥")
    ONCALL_DIR.mkdir(exist_ok=True)
    sheets = sorted(ONCALL_DIR.glob("*.xlsx"))
    st.write(f"📂 {len(sheets)} attendance sheet(s) in `oncall/`. Drop the xlsx there; green rows are national holidays.")
    if sheets:
        chosen = st.selectbox("Sheet", sheets, format_func=lambda p: p.name, key="oncall_sheet")
        try:
            available_months = oncall.months_in(chosen)
        except Exception as e:  # a broken file must never crash the app
            logger.exception("on-call sheet unreadable: %s", type(e).__name__)
            available_months = []
            st.error(f"Couldn't read that sheet ({type(e).__name__}).")
        chosen_months = st.multiselect(
            "Months to import", available_months, default=available_months[-1:],
            help="Only these month tabs are imported; months already imported earlier are left as they are.",
            key="oncall_months",
        )
        if st.button("Import on-call hours", type="primary", disabled=not chosen_months):
            backup.create_backup("before-oncall-import")
            try:
                summary = oncall.import_file(chosen, chosen_months)
            except Exception as e:  # a broken file must never crash the app
                logger.exception("on-call import failed: %s", type(e).__name__)
                st.session_state["oncall_msg"] = ("error", f"Couldn't read that sheet ({type(e).__name__}).")
            else:
                # Re-validate the invoices of the imported months right away, so the
                # Dashboard never shows results computed without the on-call extra.
                revalidated, failed = 0, 0
                for pdf_path in _invoice_pdfs():
                    who, year, month = naming.parse_filename(pdf_path)
                    if who and f"{year}-{month:02d}" in summary["months"]:
                        try:
                            process.process_invoice_file(pdf_path)
                            revalidated += 1
                        except Exception as e:
                            logger.exception("invoice processing failed: %s", type(e).__name__)
                            failed += 1
                st.session_state["oncall_summary"] = summary
                st.session_state["oncall_msg"] = (
                    "success",
                    f"Imported {summary['rows']} contractor-month(s) from {len(summary['months'])} month tab(s) "
                    f"({summary['days']} days read) and re-validated {revalidated} invoice(s) of "
                    f"{', '.join(summary['months'])}"
                    + (f" ({failed} failed, see the log)" if failed else "") + " 💅",
                )
            st.rerun()
    msg = st.session_state.pop("oncall_msg", None)
    if msg:
        getattr(st, msg[0])(msg[1])
    summary = st.session_state.get("oncall_summary")
    if summary:
        if summary["unknown_names"]:
            who = (f"{len(summary['unknown_names'])} name(s)" if view.private
                   else ", ".join(summary["unknown_names"]))
            st.info(f"Skipped as staff (not in Contracts): {who}")
        for note in summary["notes"][:20]:
            st.warning(view.text(note) if view.private else note)

    st.markdown("#### Hours and extra per contractor and month 🧮")
    oc_rows = db.list_oncall_hours()
    if not oc_rows:
        st.info("No on-call hours imported yet.")
    else:
        rates = oncall.get_rates()
        fx_cache = {}

        def _rate(month):
            """ECB reference rate (USD per EUR) for the month's last day — the same
            lookup (and DB cache) the Dashboard uses; None when the ECB has no rate
            yet (current month)."""
            if month not in fx_cache:
                try:
                    year, mon = (int(x) for x in month.split("-"))
                    fx_cache[month] = fx.get_rate_for_month(year, mon)["rate"]
                except (fx.FxUnavailable, ValueError):
                    fx_cache[month] = None
            return fx_cache[month]

        table = []
        for r in oc_rows:
            terms = db.get_effective_contract_terms(r["contractor_id"], r["service_month"])
            calc = oncall.compute_extra(r, terms[0]["amount_eur"], rates) if len(terms) == 1 else None
            rate = _rate(r["service_month"]) if calc else None
            table.append({
                "Contractor": view.who(r["contractor_name"]),
                "Month": r["service_month"],
                "Standby": r["standby_standard"],
                "Standby (holiday)": r["standby_holiday"],
                "Active evening": r["active_evening"],
                "Active night": r["active_night"],
                "Active (holiday)": r["active_holiday"],
                "Hourly EUR": view.amount(calc["hourly_eur"]) if calc else "no contract term",
                "Extra EUR": view.amount(calc["total_eur"]) if calc else "—",
                "Extra USD": view.amount(round(calc["total_eur"] * rate, 2)) if calc and rate else "—",
                "ECB Rate": f"{rate:.4f}" if rate else "—",
                "Check": ("⚠️ unreadable entry" if r["unreadable"] else "") + (" 📝" if r["notes"] else ""),
            })
        st.dataframe(pd.DataFrame(table), width="stretch", height="content", hide_index=True)
        with st.expander("Notes from the import"):
            notes = [f"{view.who(r['contractor_name'])} {r['service_month']}: {view.text(n) if view.private else n}"
                     for r in oc_rows for n in r["notes"]]
            st.write("\n\n".join(notes) if notes else "No notes — every entry was read cleanly.")

with tab_contracts:
    st.subheader("Contract amounts 📑")
    st.caption(
        "Each term is the contracted monthly amount in EUR for a range of months. "
        "Contractors are added automatically when you validate their invoices, or you can add one below."
    )

    # --- Overview: what each person earns, and what that is per manday and hour ---
    st.markdown("#### Who earns what 💼")
    st.caption(
        f"Current contract per person. Manday = monthly amount ÷ {oncall.WORKING_DAYS_PER_MONTH} working days; "
        f"hour = manday ÷ {oncall.HOURS_PER_DAY}. These are the rates the on-call extra is built on."
    )
    this_month = dt.date.today().strftime("%Y-%m")
    overview = []
    for c in sorted(db.list_contractors(), key=lambda c: naming.surname_sort_key(c["name"])):
        terms_c = db.list_contract_terms(c["id"])
        current = [t for t in terms_c if t["effective_from"] <= this_month and (t["effective_to"] is None or t["effective_to"] >= this_month)]
        term = current[0] if current else (max(terms_c, key=lambda t: t["effective_from"]) if terms_c else None)
        amount = term["amount_eur"] if term else None
        adj = (term["adjustment_eur"] or 0.0) if term else 0.0
        overview.append({
            "Contractor": view.who(c["name"]),
            "Monthly EUR": view.amount(amount),
            "Manday EUR": view.amount(round(amount / oncall.WORKING_DAYS_PER_MONTH, 2)) if amount else "—",
            "Hour EUR": view.amount(round(amount / oncall.WORKING_DAYS_PER_MONTH / oncall.HOURS_PER_DAY, 2)) if amount else "—",
            "Adjustment EUR": view.amount(adj) if adj else "",
            "Valid": (f"{term['effective_from']} → {term['effective_to'] or 'open-ended'}" if term else "no contract yet")
                     + ("" if current or not term else " (past)"),
            "Due the contract": f"{int(c['due_days'])} days" if c["due_days"] else "—",
            "Terms": len(terms_c),
        })
    if overview:
        st.dataframe(
            pd.DataFrame(overview), width="stretch", height="content", hide_index=True,
            column_config={
                "Terms": st.column_config.NumberColumn("Terms", help="How many contract terms this person has (see below)"),
                "Valid": st.column_config.TextColumn("Valid", help="Months the shown amount applies to"),
            },
        )
    else:
        st.info("No contractors yet. Validate invoices or add one below.")

    if view.private:
        st.info("🙈 Privacy mode is on: contract amounts are hidden and editing is paused.")
        for c in sorted(db.list_contractors(), key=lambda c: naming.surname_sort_key(c["name"])):
            name = c["name"]
            periods = [f"{t['effective_from']} to {t['effective_to'] or 'open-ended'}"
                       for t in db.list_contract_terms(c["id"])]
            st.markdown(f"**{view.who(name)}** · " + (", ".join(periods) if periods else "no terms yet"))
    else:
        def _flash(kind, text):
            st.session_state["contracts_msg"] = (kind, text)

        def _add_contractor():
            name = unicodedata.normalize("NFC", st.session_state.get("new_contractor_name", "")).strip()
            if not name:
                _flash("error", "Type a contractor name first.")
                return
            before = {c["id"] for c in db.list_contractors()}
            cid = db.upsert_contractor(name)
            actual = db.get_contractor_name(cid)
            st.session_state["contracts_selected"] = actual
            st.session_state["new_contractor_name"] = ""
            if cid not in before:
                _flash("success", f"{naming.display_name(actual)} added and is selected below.")
            elif actual != name:
                _flash("info", f"'{name}' matches the existing contractor {naming.display_name(actual)} (spelling from the invoice), which is selected below.")
            else:
                _flash("info", f"{naming.display_name(actual)} already exists and is selected below.")

        def _add_term(contractor_id, contractor_name):
            error, start, end = contracts.check_new_term(
                st.session_state.get("term_amount"),
                st.session_state.get("term_from", ""),
                st.session_state.get("term_to", ""),
                db.list_contract_terms(contractor_id),
            )
            if error:
                _flash("error", f"Not saved: {error}")
                return
            db.add_contract_term(contractor_id, float(st.session_state["term_amount"]), start, end)
            for k in ("term_amount", "term_from", "term_to"):
                st.session_state.pop(k, None)
            _flash("success", f"Saved for {naming.display_name(contractor_name)}: {start} to {end or 'open-ended'}. Run Validate again to re-check invoices.")

        def _save_adjustment(term_id, label):
            adj = float(st.session_state.get(f"adj_{term_id}") or 0.0)
            note = (st.session_state.get(f"adj_note_{term_id}") or "").strip() or None
            db.set_term_adjustment(term_id, round(adj, 2), note)
            _flash("success", f"Adjustment for {label} saved as {adj:+,.2f} EUR. Run Validate again to re-check invoices.")

        def _ask_delete_term(term_id):
            st.session_state["confirm_delete_term"] = term_id

        def _cancel_delete_term():
            st.session_state.pop("confirm_delete_term", None)

        def _delete_term(term_id, label):
            backup.create_backup("before-delete")
            db.delete_contract_term(term_id)
            st.session_state.pop("confirm_delete_term", None)
            _flash("success", f"Deleted term {label}.")

        st.text_input("New contractor name (spelled exactly as in the invoice filename)", key="new_contractor_name")
        st.button("Add contractor", on_click=_add_contractor)

        msg = st.session_state.pop("contracts_msg", None)
        if msg:
            getattr(st, msg[0])(msg[1])

        names = sorted((c["name"] for c in db.list_contractors()), key=naming.surname_sort_key)
        if not names:
            st.write("No contractors yet. Add one above, or run Validate.")
        else:
            ids = {c["name"]: c["id"] for c in db.list_contractors()}
            if st.session_state.get("contracts_selected") not in names:
                st.session_state.pop("contracts_selected", None)
            selected = st.selectbox("Contractor to configure", names, key="contracts_selected", format_func=naming.display_name)
            cid = ids[selected]

            st.markdown(f"##### Payment terms for {naming.display_name(selected)}")
            due_now = next((c["due_days"] for c in db.list_contractors() if c["id"] == cid), None)
            pt_col, pt_btn = st.columns([2, 1], vertical_alignment="bottom")
            due_val = pt_col.number_input(
                "Days from the issue date (0 = not set)", min_value=0, max_value=365, value=int(due_now or 0), step=1,
                key=f"due_days_{cid}", help="Shown on the Dashboard as 'Due the contract'.",
            )
            if pt_btn.button("Save payment terms", key=f"save_due_{cid}"):
                db.set_contractor_due_days(cid, int(due_val) or None)
                st.session_state["contracts_msg"] = ("success", f"Payment terms saved: {int(due_val)} days 📅")
                st.rerun()

            st.markdown(f"##### Terms for {naming.display_name(selected)}")
            terms = db.list_contract_terms(cid)
            if not terms:
                st.write("No terms yet. Invoices for this contractor will show Manual Review until one is added.")
            for t in terms:
                label = f"{t['effective_from']} to {t['effective_to'] or 'open-ended'}"
                adj = t["adjustment_eur"] or 0.0
                c1, c2 = st.columns([5, 1], vertical_alignment="center")
                line = f"**{label}**: {t['amount_eur']:,.2f} EUR per month"
                if adj:
                    line += (
                        f"  \nStanding adjustment {adj:+,.2f} EUR, so invoices are expected to show "
                        f"**{t['amount_eur'] + adj:,.2f} EUR**"
                        + (f" ({t['adjustment_note']})" if t["adjustment_note"] else "")
                    )
                c1.markdown(line)
                c2.button("Delete", key=f"del_term_{t['id']}", on_click=_ask_delete_term, args=(t["id"],))
                if st.session_state.get("confirm_delete_term") == t["id"]:
                    st.warning(f"Delete the term {label}? Invoices in those months will go to Manual Review until a new term is added.")
                    y, n, _ = st.columns([1, 1, 4])
                    y.button("Yes, delete term", type="primary", key=f"yes_del_term_{t['id']}",
                             on_click=_delete_term, args=(t["id"], label))
                    n.button("Cancel", key=f"cancel_del_term_{t['id']}", on_click=_cancel_delete_term)
                with st.expander(f"Standing adjustment for {label}"):
                    st.caption(
                        "A fixed EUR difference this contractor's invoices always show compared with the contract "
                        "(negative = invoice is lower). The expected invoice amount becomes contract + adjustment. "
                        "Set to 0 to remove it."
                    )
                    st.number_input("Adjustment (EUR)", value=float(adj), step=1.0, format="%.2f", key=f"adj_{t['id']}")
                    st.text_input("Reason (optional)", value=t["adjustment_note"] or "", key=f"adj_note_{t['id']}")
                    st.button("Save adjustment", key=f"adj_save_{t['id']}", on_click=_save_adjustment, args=(t["id"], label))

            st.markdown(f"##### Add a term for {naming.display_name(selected)}")
            a1, a2, a3 = st.columns(3)
            a1.number_input("Monthly amount (EUR)", min_value=0.0, step=1000.0, key="term_amount")
            a2.text_input("Effective from (YYYY-MM)", key="term_from", placeholder="2026-09")
            a3.text_input("Effective to (YYYY-MM)", key="term_to", placeholder="blank = open-ended")
            st.button(f"Save term for {naming.display_name(selected)}", type="primary", on_click=_add_term, args=(cid, selected))
