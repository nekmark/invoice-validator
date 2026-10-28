"""On-call extra pay, computed the way the on-call calculator does it.

Hourly rate = contract EUR for the month / 20 working days / 8 hours.
Each kind of hour x hourly rate x its multiplier, summed:

    standby on a normal day        x 0.10
    standby on a national holiday  x 0.15
    active work in the evening     x 1.35
    active work after 10 pm        x 1.45
    active work on a holiday       x 2.50

Hours come from the on-call attendance sheet (one xlsx, one tab per month,
one row per day): the Name columns say who was on call, "Standby hrs" is the
standby time, "On-call work hrs" is free text like "about 2h" (active work),
"Night shift" is active work after 10 pm, and a GREEN row is a national
holiday. People who are not in the Contracts tab are staff and are skipped.

The xlsx is read with the standard library only (zip + XML): no new
dependency, no network, nothing leaves the computer."""

from __future__ import annotations

import datetime as dt
import json
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from pipeline import db
from pipeline.extraction import normalize

WORKING_DAYS_PER_MONTH = 20
HOURS_PER_DAY = 8

# (key, label, default multiplier) — order is the order shown in the app.
KINDS = [
    ("standby_standard", "Standby, normal day", 0.10),
    ("standby_holiday", "Standby, national holiday", 0.15),
    ("active_evening", "Active work, evening", 1.35),
    ("active_night", "Active work, after 10 pm", 1.45),
    ("active_holiday", "Active work, national holiday", 2.50),
]
KIND_KEYS = [k for k, _, _ in KINDS]
LABELS = {k: label for k, label, _ in KINDS}
DEFAULT_RATES = {k: rate for k, _, rate in KINDS}
RATES_SETTING = "oncall_rates"


# --- multipliers (stored locally in the settings table) ---------------------

def get_rates() -> dict:
    raw = db.get_setting(RATES_SETTING)
    rates = dict(DEFAULT_RATES)
    if raw:
        try:
            rates.update({k: float(v) for k, v in json.loads(raw).items() if k in DEFAULT_RATES})
        except (ValueError, TypeError, AttributeError):
            pass
    return rates


def set_rates(rates: dict):
    clean = {k: float(rates.get(k, DEFAULT_RATES[k])) for k in KIND_KEYS}
    if any(v < 0 for v in clean.values()):
        raise ValueError("Multipliers can't be negative.")
    db.set_setting(RATES_SETTING, json.dumps(clean))


# --- the calculation --------------------------------------------------------

def compute_extra(hours: dict, contract_eur: float, rates: dict | None = None) -> dict:
    """hours: {kind: hours}. Returns manday/hourly rates, one line per kind
    and the total extra in EUR (rounded to cents)."""
    rates = rates or get_rates()
    manday = contract_eur / WORKING_DAYS_PER_MONTH
    hourly = manday / HOURS_PER_DAY
    lines = []
    total = 0.0
    for kind in KIND_KEYS:
        h = float(hours.get(kind) or 0.0)
        amount = round(h * hourly * rates[kind], 2)
        total += amount
        lines.append({"kind": kind, "label": LABELS[kind], "hours": h, "rate": rates[kind], "amount": amount})
    return {"manday_eur": round(manday, 2), "hourly_eur": round(hourly, 4), "lines": lines, "total_eur": round(total, 2)}


def hours_summary(hours: dict) -> str:
    """'20 h standby, 2 h active evening' — only the non-zero kinds."""
    short = {"standby_standard": "standby", "standby_holiday": "holiday standby", "active_evening": "active evening",
             "active_night": "active night", "active_holiday": "active holiday"}
    parts = [f"{float(hours.get(k) or 0):g} h {short[k]}" for k in KIND_KEYS if float(hours.get(k) or 0)]
    return ", ".join(parts) if parts else "no hours"


# --- reading the attendance sheet -------------------------------------------

_HOURS_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:h\b|hr\b|hrs\b|hour|hod)", re.IGNORECASE)
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")


def parse_hours(text) -> float | None:
    """'about 2h' -> 2.0, '(something) 1 hour' -> 1.0, '' -> 0.0.
    None when the text has no readable number of hours."""
    if text is None:
        return 0.0
    if isinstance(text, (int, float)):
        return float(text)
    s = str(text).strip()
    if not s:
        return 0.0
    try:
        return float(s.replace(",", "."))
    except ValueError:
        pass
    m = _HOURS_RE.search(s)
    if m:
        return float(m.group(1).replace(",", "."))
    numbers = _NUMBER_RE.findall(s)
    if len(numbers) == 1:
        return float(numbers[0].replace(",", "."))
    return None


_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
_M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _is_green(rgb: str | None) -> bool:
    """Light/solid green fill (the holiday marker): green channel dominates."""
    if not rgb or len(rgb) < 6:
        return False
    hexpart = rgb[-6:]
    try:
        r, g, b = (int(hexpart[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return False
    return g > r and g > b


def _fill_colours(z: zipfile.ZipFile) -> list[str | None]:
    """cellXfs index -> fill rgb (None when no solid fill)."""
    try:
        styles = ET.fromstring(z.read("xl/styles.xml"))
    except KeyError:
        return []
    fills = []
    fills_el, xfs_el = styles.find("m:fills", _NS), styles.find("m:cellXfs", _NS)
    for f in (fills_el if fills_el is not None else []):
        pf = f.find("m:patternFill", _NS)
        fg = pf.find("m:fgColor", _NS) if pf is not None else None
        fills.append(fg.get("rgb") if (pf is not None and pf.get("patternType") == "solid" and fg is not None) else None)
    out = []
    for xf in (xfs_el if xfs_el is not None else []):
        fid = int(xf.get("fillId", 0))
        out.append(fills[fid] if fid < len(fills) else None)
    return out


def _cell_text(c, shared: list[str]) -> str | None:
    t = c.get("t")
    if t == "inlineStr":
        return "".join(x.text or "" for x in c.iter(f"{_M}t"))
    v = c.find("m:v", _NS)
    if v is None or v.text is None:
        return None
    if t == "s":
        return shared[int(v.text)]
    return v.text


def _parse_date(raw: str | None) -> dt.date | None:
    if not raw:
        return None
    raw = raw.strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%d. %m. %Y"):
        try:
            return dt.datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    try:
        serial = float(raw)
    except ValueError:
        return None
    if 20000 < serial < 80000:  # Excel serial date
        return dt.date(1899, 12, 30) + dt.timedelta(days=int(serial))
    return None


def read_attendance(path: Path) -> list[dict]:
    """Every dated row of every tab: {date, names, standby_hours, work_text,
    night_text, holiday, sheet}. Columns are found by their header text, so
    an extra Name column or a moved column doesn't matter."""
    path = Path(path)
    days = []
    with zipfile.ZipFile(path) as z:
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
        try:
            shared = ["".join(t.text or "" for t in si.iter(f"{_M}t"))
                      for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", _NS)]
        except KeyError:
            shared = []
        xf_fill = _fill_colours(z)
        for sh in wb.find("m:sheets", _NS):
            target = rels[sh.get(f"{_R}id")]
            target = target if target.startswith("xl/") else f"xl/{target.lstrip('/')}"
            root = ET.fromstring(z.read(target))
            header = None
            for row in root.find("m:sheetData", _NS).findall("m:row", _NS):
                cells, fills = {}, []
                for c in row.findall("m:c", _NS):
                    col = re.match(r"[A-Z]+", c.get("r")).group(0)
                    cells[col] = _cell_text(c, shared)
                    s = int(c.get("s", 0))
                    fills.append(xf_fill[s] if s < len(xf_fill) else None)
                texts = {col: (v or "").strip() for col, v in cells.items()}
                if any(v.lower() == "date" for v in texts.values()):
                    header = {"names": [], "standby": None, "work": None, "night": None, "date": None}
                    for col, v in texts.items():
                        lv = v.lower()
                        if lv == "date":
                            header["date"] = col
                        elif lv == "name":
                            header["names"].append(col)
                        elif lv.startswith("standby"):
                            header["standby"] = col
                        elif "work" in lv and "hrs" in lv or lv.startswith("on-call work") or lv.startswith("on call work"):
                            header["work"] = col
                        elif lv.startswith("night"):
                            header["night"] = col
                    continue
                if not header or not header["date"]:
                    continue
                date = _parse_date(cells.get(header["date"]))
                if date is None:
                    continue
                names = [texts[c] for c in header["names"] if texts.get(c)]
                days.append({
                    "date": date,
                    "sheet": sh.get("name"),
                    "names": names,
                    "standby_hours": parse_hours(cells.get(header["standby"])) if header["standby"] else 0.0,
                    "work_text": cells.get(header["work"]) if header["work"] else None,
                    "night_text": cells.get(header["night"]) if header["night"] else None,
                    "holiday": any(_is_green(f) for f in fills),
                })
    return days


# --- matching names and summing a month -------------------------------------

def _name_key(name: str) -> frozenset:
    return frozenset(normalize(name).split())


def match_contractor(name: str, contractors: list[str]) -> str | None:
    """The contractor whose name has the same words (any order, accents and
    case ignored), or None (= staff, skipped)."""
    key = _name_key(name)
    if not key:
        return None
    for c in contractors:
        if _name_key(c) == key:
            return c
    return None


def month_hours(days: list[dict], contractors: list[str]) -> tuple[list[dict], list[str]]:
    """Sums hours per (contractor, month). Returns (rows, unknown_names).
    Each row: contractor, month, the five hour kinds, unreadable (bool),
    notes (list of str)."""
    rows: dict[tuple[str, str], dict] = {}
    unknown: set[str] = set()
    for day in days:
        month = day["date"].strftime("%Y-%m")
        matched = []
        for n in day["names"]:
            c = match_contractor(n, contractors)
            if c is None:
                unknown.add(n.strip())
            elif c not in matched:
                matched.append(c)
        if not matched:
            continue
        work = parse_hours(day["work_text"])
        night = parse_hours(day["night_text"])
        for c in matched:
            row = rows.setdefault((c, month), {
                "contractor": c, "month": month, **{k: 0.0 for k in KIND_KEYS}, "unreadable": False, "notes": [],
            })
            standby = day["standby_hours"]
            if standby is None:
                row["unreadable"] = True
                row["notes"].append(f"{day['date'].isoformat()}: unreadable standby hours")
                standby = 0.0
            row["standby_holiday" if day["holiday"] else "standby_standard"] += standby
            if work is None:
                row["unreadable"] = True
                row["notes"].append(f"{day['date'].isoformat()}: unreadable on-call work entry")
                work = 0.0
            if night is None:
                row["unreadable"] = True
                row["notes"].append(f"{day['date'].isoformat()}: unreadable night shift entry")
                night = 0.0
            if day["holiday"]:
                row["active_holiday"] += work + night
            else:
                row["active_evening"] += work
                row["active_night"] += night
            if (work or night) and len(matched) > 1:
                row["notes"].append(
                    f"{day['date'].isoformat()}: active work on a day with {len(matched)} contractors on call — "
                    "counted for each of them, check who did it"
                )
    out = sorted(rows.values(), key=lambda r: (r["month"], r["contractor"]))
    for r in out:
        for k in KIND_KEYS:
            r[k] = round(r[k], 2)
    return out, sorted(unknown)


def months_in(path: Path) -> list[str]:
    """The months ('YYYY-MM') that have at least one dated row in the sheet."""
    return sorted({d["date"].strftime("%Y-%m") for d in read_attendance(path)})


def import_file(path: Path, months: list[str] | None = None) -> dict:
    """Reads the sheet, matches names to the Contracts tab, stores one row
    per contractor and month (replacing earlier imports of the same month).
    months: only these 'YYYY-MM' months (default: every month in the sheet).
    Returns a summary with counts, months, skipped names and notes."""
    path = Path(path)
    days = read_attendance(path)
    if months is not None:
        days = [d for d in days if d["date"].strftime("%Y-%m") in set(months)]
    contractors = [c["name"] for c in db.list_contractors()]
    rows, unknown = month_hours(days, contractors)
    ids = {c["name"]: c["id"] for c in db.list_contractors()}
    stamp = dt.datetime.now().isoformat(timespec="seconds")
    for r in rows:
        db.upsert_oncall_hours(ids[r["contractor"]], r["month"], {k: r[k] for k in KIND_KEYS},
                               r["unreadable"], r["notes"], path.name, stamp)
    return {
        "file": path.name,
        "days": len(days),
        "rows": len(rows),
        "months": sorted({r["month"] for r in rows}),
        "contractors": sorted({r["contractor"] for r in rows}),
        "unknown_names": unknown,
        "notes": [f"{r['contractor']} {r['month']} — {n}" for r in rows for n in r["notes"]],
    }
