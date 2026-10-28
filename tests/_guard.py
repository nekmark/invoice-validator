"""Hard stop if a test would touch the real database.

tests/conftest.py points every test at a throwaway DB. If test code is ever
run without it (imported into another script, run from another directory,
`-c /dev/null`, …) this autouse fixture fails the test before it can read or
write data/app.db. Every test module imports it.
"""

from pathlib import Path

import pytest

from pipeline import db

REAL_DB = (Path(__file__).resolve().parent.parent / "data" / "app.db").resolve()


def check_not_real_db():
    if Path(db.DB_PATH).resolve() == REAL_DB or Path(db.DATA_DIR).resolve() == REAL_DB.parent:
        raise RuntimeError(
            "Refusing to run: this test would use the REAL database (data/app.db). "
            "Run tests only with `python -m pytest tests/` from the project folder."
        )


@pytest.fixture(autouse=True)
def refuse_real_database():
    check_not_real_db()
    yield
