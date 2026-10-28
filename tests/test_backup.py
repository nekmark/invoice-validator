from tests._guard import refuse_real_database  # noqa: F401  (stops tests touching the real DB)
import datetime as dt
import os

import pytest

from pipeline import backup, db


def _contractors():
    return [c["name"] for c in db.list_contractors()]


def test_backup_and_restore_round_trip():
    db.upsert_contractor("Before Example")
    b = backup.create_backup("manual")
    assert oct(os.stat(b).st_mode)[-3:] == "600"
    assert oct(os.stat(b.parent).st_mode)[-3:] == "700"
    db.upsert_contractor("After Example")
    safety = backup.restore_backup(b)
    assert _contractors() == ["Before Example"]
    backup.restore_backup(safety)  # the restore itself can be undone
    assert sorted(_contractors()) == ["After Example", "Before Example"]


def test_daily_backup_only_when_due(monkeypatch):
    first = backup.auto_backup_if_due()
    assert first is not None and "daily" in first.name
    assert backup.auto_backup_if_due() is None  # already backed up today

    real_now = dt.datetime.now()

    class Later(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return real_now + dt.timedelta(hours=25)

    monkeypatch.setattr(backup.dt, "datetime", Later)
    assert backup.auto_backup_if_due() is not None


def test_only_newest_backups_are_kept():
    for _ in range(backup.KEEP + 5):
        backup.create_backup("manual")
    assert len(backup.list_backups()) == backup.KEEP


def test_restoring_the_oldest_backup_when_full_still_works():
    db.upsert_contractor("Oldest Example")
    oldest = backup.create_backup("manual")
    db.upsert_contractor("Newer Example")
    for _ in range(backup.KEEP - 1):
        backup.create_backup("manual")
    assert backup.list_backups()[-1]["path"] == oldest
    backup.restore_backup(oldest)
    assert _contractors() == ["Oldest Example"]
    assert len(backup.list_backups()) == backup.KEEP


def test_restore_refuses_files_outside_backups(tmp_path):
    stray = tmp_path / "app-20260101-000000-manual.db"
    stray.write_bytes(b"not a db")
    with pytest.raises(ValueError):
        backup.restore_backup(stray)


def test_same_second_backups_with_different_reasons_keep_their_order(monkeypatch):
    fixed = dt.datetime(2026, 10, 19, 12, 0, 0)

    class Frozen(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(backup.dt, "datetime", Frozen)
    a = backup.create_backup("daily")
    b = backup.create_backup("manual")
    c = backup.create_backup("before-restore")
    assert [x["path"] for x in backup.list_backups()] == [c, b, a]
