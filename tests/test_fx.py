from tests._guard import refuse_real_database  # noqa: F401  (stops tests touching the real DB)
import datetime

import pytest

from pipeline import fx

# What the ECB Data Portal returns for a window ending on Saturday 31 Jan
# 2026: the last published business day is Friday 30 Jan.
SAMPLE_RESPONSE = """KEY,FREQ,CURRENCY,CURRENCY_DENOM,EXR_TYPE,EXR_SUFFIX,TIME_PERIOD,OBS_VALUE,OBS_STATUS
EXR.D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,2026-01-28,1.0790,A
EXR.D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,2026-01-29,1.0812,A
EXR.D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,2026-01-30,1.0850,A
"""


def test_last_day_of_month_handles_leap_year():
    assert fx.last_day_of_month(2026, 2) == datetime.date(2026, 2, 28)
    assert fx.last_day_of_month(2024, 2) == datetime.date(2024, 2, 29)


def test_request_url_carries_only_the_date_window():
    url = fx._request_url(datetime.date(2026, 1, 31))
    assert url == f"{fx.ECB_URL}?startPeriod=2026-01-21&endPeriod=2026-01-31&format=csvdata"


def test_fetch_rate_for_date_takes_the_latest_published_day(monkeypatch):
    monkeypatch.setattr(fx, "_fetch_raw", lambda d: SAMPLE_RESPONSE)
    result = fx.fetch_rate_for_date(datetime.date(2026, 1, 31))
    assert result["rate"] == 1.085
    assert result["effective_date"] == "2026-01-30"
    assert result["requested_date"] == "2026-01-31"


def test_fetch_rate_rejects_implausible_gap(monkeypatch):
    # SAMPLE_RESPONSE ends on 2026-01-30; asking for a date two months later
    # and getting that back must be rejected, not trusted.
    monkeypatch.setattr(fx, "_fetch_raw", lambda d: SAMPLE_RESPONSE)
    with pytest.raises(fx.FxUnavailable):
        fx.fetch_rate_for_date(datetime.date(2026, 3, 31))


def test_fetch_rate_rejects_future_effective_date(monkeypatch):
    # A rate dated after the requested date should never happen but must be rejected defensively.
    future_response = SAMPLE_RESPONSE.replace("2026-01-30", "2026-02-28")
    monkeypatch.setattr(fx, "_fetch_raw", lambda d: future_response)
    with pytest.raises(fx.FxUnavailable):
        fx.fetch_rate_for_date(datetime.date(2026, 1, 31))


def test_empty_response_is_unavailable_not_a_guess(monkeypatch):
    # The ECB answers a window with no observations (e.g. the current month
    # before publication) with just the header, or with nothing at all.
    for body in ("", "KEY,FREQ,CURRENCY,CURRENCY_DENOM,EXR_TYPE,EXR_SUFFIX,TIME_PERIOD,OBS_VALUE,OBS_STATUS\n"):
        monkeypatch.setattr(fx, "_fetch_raw", lambda d, body=body: body)
        with pytest.raises(fx.FxUnavailable):
            fx.fetch_rate_for_date(datetime.date(2026, 1, 31))


def test_garbled_response_is_unavailable(monkeypatch):
    monkeypatch.setattr(fx, "_fetch_raw", lambda d: "TIME_PERIOD,OBS_VALUE\nnot-a-date,abc\n")
    with pytest.raises(fx.FxUnavailable):
        fx.fetch_rate_for_date(datetime.date(2026, 1, 31))


def test_get_rate_for_month_uses_cache(monkeypatch):
    calls = {"n": 0}

    def fake_fetch(d):
        calls["n"] += 1
        return SAMPLE_RESPONSE

    monkeypatch.setattr(fx, "_fetch_raw", fake_fetch)
    r1 = fx.get_rate_for_month(2026, 1)
    r2 = fx.get_rate_for_month(2026, 1)
    assert calls["n"] == 1
    assert r1["rate"] == r2["rate"] == 1.085
    assert r1["from_cache"] is False
    assert r2["from_cache"] is True


def test_network_failure_raises_unavailable(monkeypatch):
    def raise_err(d):
        raise OSError("network down")

    monkeypatch.setattr(fx, "_fetch_raw", raise_err)
    with pytest.raises(fx.FxUnavailable):
        fx.fetch_rate_for_date(datetime.date(2026, 1, 31))


def test_cache_hit_reports_ecb_publication_date(monkeypatch):
    # Jan 31 2026 is a Saturday; the ECB published for Jan 30. The cached
    # lookup must still report Jan 30, not the requested Jan 31.
    monkeypatch.setattr(fx, "_fetch_raw", lambda d: SAMPLE_RESPONSE)
    first = fx.get_rate_for_month(2026, 1)
    second = fx.get_rate_for_month(2026, 1)
    assert second["from_cache"] is True
    assert first["effective_date"] == second["effective_date"] == "2026-01-30"
    assert second["requested_date"] == "2026-01-31"


def test_legacy_cache_row_without_publication_date_is_refetched(monkeypatch):
    from pipeline import db

    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO fx_rates (effective_date, rate_usd_per_eur, source_url, retrieved_at) VALUES (?, ?, ?, ?)",
            ("2026-01-31", 1.085, "https://example.invalid", "2026-02-01T00:00:00"),
        )
    calls = {"n": 0}

    def fake_fetch(d):
        calls["n"] += 1
        return SAMPLE_RESPONSE

    monkeypatch.setattr(fx, "_fetch_raw", fake_fetch)
    result = fx.get_rate_for_month(2026, 1)
    assert calls["n"] == 1
    assert result["effective_date"] == "2026-01-30"
