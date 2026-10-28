"""Pre-commit guard: refuse to commit confidential files or real contractor
names. Real names are read at run time from the local database (read-only),
so they never have to be written down anywhere in the repository."""

import re
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pipeline.extraction import extract_text, normalize  # noqa: E402

BLOCKED = re.compile(r"^(invoices|data|exports|oncall)/|secrets\.local\.json$|\.db(-journal|-wal|-shm)?$|\.csv$|\.xlsx$")


def staged_files():
    out = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"],
                         capture_output=True, text=True, cwd=ROOT, check=True).stdout
    return [f for f in out.split("\0") if f]


def real_surnames():
    db = ROOT / "data" / "app.db"
    if not db.exists():
        return set()
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        if is_demo_database(conn):
            return set()
        names = [r[0] for r in conn.execute("SELECT name FROM contractors")]
    finally:
        conn.close()
    return {normalize(n.split()[-1]) for n in names if n.split() and len(n.split()[-1]) > 2}


def is_demo_database(conn) -> bool:
    """True when scripts/make_demo.py filled this database: its names are
    fictional and may legitimately appear in the repository."""
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = 'demo_data'").fetchone()
    except sqlite3.OperationalError:  # no settings table (very old database)
        return False
    return bool(row and row[0] == "1")


def main():
    problems = []
    surnames = real_surnames()
    for f in staged_files():
        if BLOCKED.search(f) or (f.lower().endswith(".pdf") and not f.startswith("tests/fixtures/")):
            problems.append(f"{f}: confidential file type/location")
            continue
        path = ROOT / f
        try:
            text = extract_text(path)[0] if f.lower().endswith(".pdf") else path.read_text(errors="ignore")
        except Exception:
            continue
        text = normalize(text + " " + f)
        if any(re.search(r"\b" + re.escape(s) + r"\b", text) for s in surnames):
            problems.append(f"{f}: contains a real contractor surname")
    if problems:
        print("Commit blocked — confidential content staged:", *problems, sep="\n  ")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
