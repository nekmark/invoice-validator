from tests._guard import refuse_real_database  # noqa: F401  (stops tests touching the real DB)
import pytest

from pipeline import contracts


@pytest.mark.parametrize("raw, expected", [
    ("2026-09", "2026-09"), ("2026-9", "2026-09"), ("2026, 09", "2026-09"), ("2026/09", "2026-09"),
    (" 2026 09 ", "2026-09"), ("2026.9", "2026-09"), ("09-2026", None), ("2026-13", None), ("", None), ("sept", None),
])
def test_normalize_month(raw, expected):
    assert contracts.normalize_month(raw) == expected


def T(f, t):
    return {"effective_from": f, "effective_to": t}


def test_valid_term():
    assert contracts.check_new_term(100000, "2026, 9", "", []) == (None, "2026-09", None)


@pytest.mark.parametrize("amount, f, t, existing", [
    (0, "2026-09", "", []),
    (1000, "bad", "", []),
    (1000, "2026-09", "2026-08", []),
    (1000, "2026-09", "", [T("2026-01", None)]),           # open-ended existing covers it
    (1000, "2025-01", "2026-02", [T("2026-02", "2026-06")]),
    (1000, "2026-03", None, [T("2026-01", "2026-12")]),
])
def test_rejected_terms(amount, f, t, existing):
    assert contracts.check_new_term(amount, f, t, existing)[0] is not None


def test_adjacent_terms_allowed():
    assert contracts.check_new_term(1000, "2026-07", "", [T("2026-01", "2026-06")])[0] is None
    assert contracts.check_new_term(1000, "2025-01", "2025-12", [T("2026-01", None)])[0] is None
