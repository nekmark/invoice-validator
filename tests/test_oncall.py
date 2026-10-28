"""On-call extra: the calculator, the xlsx reader, name matching, import."""

from tests._guard import refuse_real_database  # noqa: F401  (stops tests touching the real DB)
import datetime as dt

import pytest

from pipeline import db, oncall
from tests.fixtures.make_oncall_xlsx import GREEN, HEADER, sample_months, write_attendance_xlsx


@pytest.fixture
def sheet(tmp_path):
    return write_attendance_xlsx(tmp_path / "attendance.xlsx", sample_months())


def test_parse_hours_reads_free_text():
    assert oncall.parse_hours("") == 0.0 and oncall.parse_hours(None) == 0.0
    assert oncall.parse_hours("2h") == 2.0 and oncall.parse_hours("about 2h") == 2.0
    assert oncall.parse_hours("(fixing a thing) 1 hour") == 1.0
    assert oncall.parse_hours("roughly 1,5 hrs (XX-12345)") == 1.5
    assert oncall.parse_hours("2h (XX-12345 - and one more ticket)") == 2.0
    assert oncall.parse_hours(5) == 5.0 and oncall.parse_hours("14") == 14.0
    assert oncall.parse_hours("no idea") is None  # unreadable -> flagged, never guessed


def test_compute_extra_follows_the_calculator():
    calc = oncall.compute_extra(
        {"standby_standard": 20, "standby_holiday": 14, "active_evening": 2, "active_night": 1, "active_holiday": 1},
        4_000.0, oncall.DEFAULT_RATES,
    )
    assert calc["manday_eur"] == 200.0 and calc["hourly_eur"] == 25.0
    by_kind = {line["kind"]: line["amount"] for line in calc["lines"]}
    assert by_kind == {"standby_standard": 50.0, "standby_holiday": 52.5, "active_evening": 67.5,
                       "active_night": 36.25, "active_holiday": 62.5}
    assert calc["total_eur"] == 268.75
    assert oncall.compute_extra({}, 4_000.0, oncall.DEFAULT_RATES)["total_eur"] == 0.0


def test_rates_are_stored_locally_and_default():
    assert oncall.get_rates() == oncall.DEFAULT_RATES
    oncall.set_rates({**oncall.DEFAULT_RATES, "active_evening": 1.5})
    assert oncall.get_rates()["active_evening"] == 1.5 and oncall.get_rates()["standby_standard"] == 0.10
    with pytest.raises(ValueError):
        oncall.set_rates({**oncall.DEFAULT_RATES, "active_night": -1})


def test_read_attendance_finds_columns_dates_and_green_rows(sheet):
    days = oncall.read_attendance(sheet)
    assert [d["date"] for d in days][:3] == [dt.date(2026, 1, 1), dt.date(2026, 1, 2), dt.date(2026, 1, 3)]
    first = days[0]
    assert first["holiday"] and first["names"] == ["Novak Mia", "Dupont Émile"] and first["standby_hours"] == 14.0
    assert first["work_text"] == "1h"
    assert not days[1]["holiday"] and not days[2]["holiday"]  # orange weekend is not a holiday
    assert days[2]["names"] == [] and days[2]["standby_hours"] == 0.0
    assert days[6]["night_text"] == "1h"
    assert {d["sheet"] for d in days} == {"Jan", "Feb"}


def test_read_attendance_copes_with_an_extra_name_column(tmp_path):
    header = ["Date", "Name", "Name", "Name"] + HEADER[3:]
    months = {"Mar": {"header": header, "rows": [
        (None, ["02.03.2026", "A B", "Dupont Émile", "C D", 5, "2h", "", "", ""]),
        (GREEN, [46115, "", "Dupont Émile", "", 14, "", "", "", ""]),  # Excel serial date = 2026-04-03
    ]}}
    days = oncall.read_attendance(write_attendance_xlsx(tmp_path / "x.xlsx", months))
    assert days[0]["names"] == ["A B", "Dupont Émile", "C D"] and days[0]["work_text"] == "2h"
    assert days[1]["date"] == dt.date(2026, 4, 3) and days[1]["holiday"]


def test_match_contractor_ignores_order_accents_and_case():
    contractors = ["Émile Dupont", "Zoë Lindqvist"]
    assert oncall.match_contractor("Dupont Émile", contractors) == "Émile Dupont"
    assert oncall.match_contractor("zoe lindqvist", contractors) == "Zoë Lindqvist"
    assert oncall.match_contractor("Mia Novak", contractors) is None
    assert oncall.match_contractor("", contractors) is None


def test_month_hours_sums_per_contractor_and_skips_staff(sheet):
    rows, unknown = oncall.month_hours(oncall.read_attendance(sheet), ["Émile Dupont", "Zoë Lindqvist"])
    assert unknown == ["Novak Mia"]
    by = {(r["contractor"], r["month"]): r for r in rows}
    jiri = by[("Émile Dupont", "2026-01")]
    assert (jiri["standby_standard"], jiri["standby_holiday"]) == (21.0, 14.0)   # 5+5+5+6 normal, 14 on the holiday
    assert (jiri["active_evening"], jiri["active_night"], jiri["active_holiday"]) == (2.0, 1.0, 1.0)
    assert not jiri["unreadable"] and jiri["notes"] == []
    sarka = by[("Zoë Lindqvist", "2026-01")]
    assert sarka["standby_standard"] == 10.0 and sarka["unreadable"]
    assert any("2026-01-09" in n and "unreadable on-call work" in n for n in sarka["notes"])
    feb = by[("Émile Dupont", "2026-02")]
    assert feb["active_evening"] == 3.0 and any("2 contractors on call" in n for n in feb["notes"])
    assert by[("Zoë Lindqvist", "2026-02")]["active_evening"] == 3.0


def test_import_file_stores_rows_and_replaces_on_reimport(sheet):
    jiri = db.upsert_contractor("Émile Dupont")
    db.upsert_contractor("Zoë Lindqvist")
    summary = oncall.import_file(sheet)
    assert summary["rows"] == 4 and summary["months"] == ["2026-01", "2026-02"]
    assert summary["unknown_names"] == ["Novak Mia"] and summary["days"] == 10
    row = db.get_oncall_hours(jiri, "2026-01")
    assert row["standby_holiday"] == 14.0 and row["source_file"] == "attendance.xlsx" and row["notes"] == []
    assert db.get_oncall_hours(jiri, "2026-03") is None
    oncall.import_file(sheet)
    assert len(db.list_oncall_hours()) == 4  # same months replaced, not duplicated
    assert db.list_oncall_hours()[0]["contractor_name"] in ("Émile Dupont", "Zoë Lindqvist")


def test_import_only_selected_months(sheet):
    db.upsert_contractor("Émile Dupont")
    db.upsert_contractor("Zoë Lindqvist")
    assert oncall.months_in(sheet) == ["2026-01", "2026-02"]
    summary = oncall.import_file(sheet, ["2026-02"])
    assert summary["months"] == ["2026-02"] and summary["rows"] == 2 and summary["days"] == 1
    assert {r["service_month"] for r in db.list_oncall_hours()} == {"2026-02"}


def test_hours_summary_text():
    assert oncall.hours_summary({"standby_standard": 20, "active_evening": 2.5}) == "20 h standby, 2.5 h active evening"
    assert oncall.hours_summary({}) == "no hours"
