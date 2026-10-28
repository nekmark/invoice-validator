"""Local database backups: daily automatic, on demand, and before a restore.

Backups are consistent SQLite snapshots (sqlite3's online backup API, safe
while the app is running) stored next to the live DB in data/backups/,
owner-only like the rest of data/. Nothing is copied anywhere else.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import sqlite3
from pathlib import Path

from pipeline import db

KEEP = 20
_NAME_RE = re.compile(r"^app-(\d{8}-\d{6})(?:-(\d+))?-([a-z-]+)\.db$")


def backup_dir() -> Path:
    d = db.DATA_DIR / "backups"
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    return d


def _copy_db(src_path: Path, dst_path: Path):
    src = sqlite3.connect(src_path)
    dst = sqlite3.connect(dst_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def create_backup(reason: str = "manual", prune_after: bool = True) -> Path:
    reason = re.sub(r"[^a-z-]", "", reason.lower()) or "manual"
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    d = backup_dir()
    # Number backups made within the same second (whatever their reason) so
    # list_backups() can always tell which is newest.
    same_second = len(list(d.glob(f"app-{stamp}-*.db")))
    target = d / (f"app-{stamp}-{reason}.db" if same_second == 0 else f"app-{stamp}-{same_second}-{reason}.db")
    while target.exists():
        same_second += 1
        target = d / f"app-{stamp}-{same_second}-{reason}.db"
    _copy_db(db.DB_PATH, target)
    os.chmod(target, 0o600)
    if prune_after:
        prune()
    return target


def list_backups() -> list[dict]:
    """Newest first: [{path, created (datetime), reason, size}]."""
    out = []
    for p in backup_dir().glob("app-*.db"):
        m = _NAME_RE.match(p.name)
        if not m:
            continue
        out.append({
            "path": p,
            "created": dt.datetime.strptime(m.group(1), "%Y%m%d-%H%M%S"),
            "reason": m.group(3),
            "seq": int(m.group(2) or 0),  # backups made within the same second
            "size": p.stat().st_size,
        })
    return sorted(out, key=lambda b: (b["created"], b["seq"]), reverse=True)


def prune(keep: int = KEEP):
    for b in list_backups()[keep:]:
        b["path"].unlink(missing_ok=True)


def auto_backup_if_due(max_age_hours: float = 24) -> Path | None:
    """Create a 'daily' backup if the newest backup is older than max_age_hours
    (or there is none). Returns the new backup's path, or None."""
    if not db.DB_PATH.exists():
        return None
    backups = list_backups()
    if backups and dt.datetime.now() - backups[0]["created"] < dt.timedelta(hours=max_age_hours):
        return None
    return create_backup("daily")


def restore_backup(backup_path: Path) -> Path:
    """Replace the live DB with backup_path. The current DB is backed up first
    (reason 'before-restore'), so a restore can itself be undone. Returns the
    path of that safety backup."""
    backup_path = Path(backup_path).resolve()
    if backup_path.parent != backup_dir().resolve() or not _NAME_RE.match(backup_path.name):
        raise ValueError("Not a backup from data/backups/")
    # Prune only after restoring, so the backup being restored can't be the
    # one that gets pruned to make room for the safety copy.
    safety = create_backup("before-restore", prune_after=False)
    _copy_db(backup_path, db.DB_PATH)
    os.chmod(db.DB_PATH, 0o600)
    prune()
    return safety
