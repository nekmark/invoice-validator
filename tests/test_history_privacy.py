"""History calculations and privacy masking (pure functions + interface)."""

from tests._guard import refuse_real_database  # noqa: F401  (stops tests touching the real DB)
from pipeline import history
import ui_privacy


def _row(name, month, status="Pass", **kw):
    base = {"contractor_name": name, "service_month": month, "status": status, "invoice_number": "X-1",
            "standard_amount_value": None, "expected_eur": None, "usd_amount_value": None,
            "expected_usd": None, "fx_rate": None, "issue_date": None, "taxable_supply_date": None}
    base.update(kw)
    return base


def test_difference_uses_eur_then_usd():
    assert history.difference_eur(_row("A B", "2026-09", standard_amount_value=3_990.0, expected_eur=4_000.0)) == -10.0
    usd_only = _row("A B", "2026-09", usd_amount_value=4_000.0, expected_usd=4_003.0, fx_rate=1.085)
    assert history.difference_eur(usd_only) == -2.76
    assert history.difference_eur(_row("A B", "2026-09")) is None


def test_overview_and_contractor_history():
    rows = [_row("Adam Walker", "2026-09"), _row("Olivia Carter", "2026-08", "Warning"), _row("Olivia Carter", "2026-09"),
            _row("Zoe Abbott", "2026-09", "Manual Review"), _row(None, "2026-09")]
    contractors, months, cells = history.overview(rows)
    assert contractors == ["Zoe Abbott", "Olivia Carter", "Adam Walker"]  # surname order
    assert months == ["2026-08", "2026-09"]
    assert cells[("Olivia Carter", "2026-08")] == "Warning" and ("Adam Walker", "2026-08") not in cells
    assert [h["month"] for h in history.contractor_history(rows, "Olivia Carter")] == ["2026-08", "2026-09"]


def test_privacy_view_masks_and_aliases():
    v = ui_privacy.View(True, ["Olivia Carter", "Zoe Abbott", "Adam Walker"])
    assert [v.who(n) for n in ["Zoe Abbott", "Olivia Carter", "Adam Walker"]] == ["Contractor A", "Contractor B", "Contractor C"]
    assert v.amount(12_345.67) == ui_privacy.MASK and v.amount(None) == "—"
    assert v.text("INV-2026-009 is 123 EUR off") == "INV-••••-••• is ••• EUR off"
    assert v.snippet("Total due: $ 4,000.00") == ui_privacy.MASK
    off = ui_privacy.View(False, ["Olivia Carter"])
    assert off.who("Olivia Carter") == "Carter Olivia" and off.amount(1234.5) == "1,234.50"
    assert ui_privacy._letters(25) == "Z" and ui_privacy._letters(26) == "AA"


def test_success_rate_over_time():
    rows = [_row("Olivia Carter", "2026-07", "Pass"), _row("Olivia Carter", "2026-08", "Warning"),
            _row("Olivia Carter", "2026-09", "Pass"), _row("Olivia Carter", "2026-10", "Manual Review"),
            _row("Adam Walker", "2026-09", "Pass")]
    _, _, cells = history.overview(rows)
    assert history.success(cells, "Olivia Carter") == (2, 4, 50)
    assert history.success(cells, "Adam Walker") == (1, 1, 100)
    assert history.success(cells, "Nobody Example") == (0, 0, 0)
