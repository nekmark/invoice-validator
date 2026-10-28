"""European Central Bank (ECB) reference exchange rate lookup.

This is the ONLY module in the app that makes a network call. It sends
nothing but a plain date range to the public ECB Data Portal API and must
never carry any invoice, contractor, or amount data in the request.

The ECB publishes euro reference rates on business days (around 16:00 CET),
quoted as USD per 1 EUR. For the last day of a month that falls on a
weekend or a holiday, the applicable rate is the last one published before
it, so the request asks for a short window ending on the requested date and
takes the latest row.
"""

import calendar
import csv
import datetime
import io
import urllib.error
import urllib.request

from pipeline import db

ECB_URL = "https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A"
TIMEOUT_SECONDS = 10
# How many days before the requested date the latest published rate may be
# (weekends + a holiday cluster). Anything further back is not a plausible
# "rate for that day" and is refused rather than trusted.
MAX_ACCEPTABLE_GAP_DAYS = 10


class FxUnavailable(Exception):
    pass


def last_day_of_month(year: int, month: int) -> datetime.date:
    last_day = calendar.monthrange(year, month)[1]
    return datetime.date(year, month, last_day)


def _request_url(requested_date: datetime.date) -> str:
    start = requested_date - datetime.timedelta(days=MAX_ACCEPTABLE_GAP_DAYS)
    return f"{ECB_URL}?startPeriod={start.isoformat()}&endPeriod={requested_date.isoformat()}&format=csvdata"


def _fetch_raw(requested_date: datetime.date) -> str:
    req = urllib.request.Request(_request_url(requested_date), headers={"User-Agent": "invoice-validator/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        return resp.read().decode("utf-8")


def _parse_csv(text: str) -> list[tuple[datetime.date, float]]:
    """(date, USD per EUR) for every observation row, oldest first."""
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        period, value = row.get("TIME_PERIOD"), row.get("OBS_VALUE")
        if not period or value in (None, ""):
            continue
        rows.append((datetime.date.fromisoformat(period), float(value)))
    return sorted(rows)


def fetch_rate_for_date(requested_date: datetime.date) -> dict:
    """Fetch the ECB reference rate (USD per 1 EUR) applicable to
    requested_date (normally the last day of an invoiced month). Returns a
    dict with rate, effective_date (the day the ECB actually published the
    rate for), requested_date, source_url, retrieved_at. Raises FxUnavailable
    if the rate can't be retrieved or the response fails the sanity check —
    never guesses a rate."""
    url = _request_url(requested_date)
    try:
        text = _fetch_raw(requested_date)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise FxUnavailable(f"Could not reach the ECB: {e}") from e

    try:
        rows = _parse_csv(text)
    except ValueError as e:
        raise FxUnavailable(f"Could not parse the ECB response: {e}") from e
    if not rows:
        raise FxUnavailable(
            f"The ECB has not published a USD rate for {requested_date.isoformat()} yet "
            f"(no observations in the {MAX_ACCEPTABLE_GAP_DAYS} days before it)."
        )

    effective_date, rate = rows[-1]
    # Sanity check: the effective date must be on or before what we asked
    # for, and not implausibly far before it.
    gap_days = (requested_date - effective_date).days
    if gap_days < 0 or gap_days > MAX_ACCEPTABLE_GAP_DAYS:
        raise FxUnavailable(
            f"The ECB returned a rate dated {effective_date.isoformat()}, which is not "
            f"a plausible match for the requested date {requested_date.isoformat()} "
            f"(gap of {gap_days} days) — refusing to trust it."
        )
    if rate <= 0:
        raise FxUnavailable(f"The ECB returned an implausible rate ({rate}).")

    return {
        "rate": rate,
        "effective_date": effective_date.isoformat(),
        "requested_date": requested_date.isoformat(),
        "source_url": url,
        "retrieved_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }


def get_rate_for_month(year: int, month: int) -> dict:
    """Cache-first lookup of the USD-per-EUR rate for the last day of
    (year, month). Returns the same shape as fetch_rate_for_date, with an
    extra 'from_cache' bool. Raises FxUnavailable if no rate can be
    obtained (never fabricates one)."""
    requested_date = last_day_of_month(year, month)
    cached = db.get_cached_fx_rate(requested_date.isoformat())
    # Rows cached before published_date existed don't know the real ECB
    # publication date, so treat them as a miss and refetch once.
    if cached and cached["published_date"]:
        return {
            "rate": cached["rate_usd_per_eur"],
            "effective_date": cached["published_date"],
            "requested_date": requested_date.isoformat(),
            "source_url": cached["source_url"],
            "retrieved_at": cached["retrieved_at"],
            "from_cache": True,
        }

    result = fetch_rate_for_date(requested_date)
    # Cache keyed by the date we asked for (last day of invoiced month), so
    # repeat lookups for the same month hit the cache even though the actual
    # published effective_date might differ (weekend/holiday).
    db.cache_fx_rate(
        requested_date.isoformat(), result["rate"], result["source_url"], result["retrieved_at"], result["effective_date"]
    )
    result["from_cache"] = False
    return result
