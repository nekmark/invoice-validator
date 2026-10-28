"""Helpers for bringing invoice PDFs into the '<Contractor>_<YYYY-MM>.pdf'
naming convention. Suggestions are computed locally from the filename only
(never the PDF contents) and are never applied without the user confirming
them in the app — a wrong month guess would silently validate against the
wrong contract term and month-end date."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from pipeline.extraction import normalize, parse_filename

MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
# Year and month separated by - or _ and NOT followed by another digit, so
# invoice numbers like '2026-0010' or 'FV20260010' are never read as months.
_FILENAME_MONTH_RE = re.compile(r"(?<!\d)(20\d{2})[-_](0[1-9]|1[0-2])(?!\d)")


def follows_convention(pdf_path: Path) -> bool:
    name, year, _ = parse_filename(pdf_path)
    return bool(name and year)


def suggest_month(stem: str) -> str:
    m = _FILENAME_MONTH_RE.search(stem)
    return f"{m.group(1)}-{m.group(2)}" if m else ""


def _squash(s: str) -> str:
    return re.sub(r"[^a-z]", "", normalize(s))


def suggest_contractor(stem: str, known_names: list[str]) -> str:
    """A known contractor whose name (ignoring case, spaces, accents) appears
    in the filename; otherwise the leading non-digit text, tidied up."""
    squashed = _squash(stem)
    for name in sorted(known_names, key=len, reverse=True):
        if _squash(name) and _squash(name) in squashed:
            return name
    lead = re.split(r"\d", stem, maxsplit=1)[0]
    lead = re.sub(r"[_\-.]+", " ", lead)
    lead = re.sub(r"(?i)\b(invoice|faktura|inv|fv)\b", " ", lead).strip()
    return " ".join(w if any(c.isupper() for c in w) else w.capitalize() for w in lead.split())


def target_filename(contractor: str, month: str) -> str:
    return f"{contractor.strip()}_{month.strip()}.pdf"


def validate_choice(contractor: str, month: str) -> str | None:
    """Returns an error message, or None if the choice is usable."""
    contractor = (contractor or "").strip()
    if not contractor:
        return "contractor is empty"
    if re.search(r'[/\\:*?"<>|]', contractor):
        return "contractor name contains a character not allowed in filenames"
    if re.search(r"\d", contractor):
        return "contractor name must not contain digits"
    if not MONTH_RE.match((month or "").strip()):
        return "month must be YYYY-MM, e.g. 2026-09"
    return None


def rename_to_convention(pdf_path: Path, contractor: str, month: str) -> Path:
    """Renames pdf_path in place. Never overwrites an existing file."""
    error = validate_choice(contractor, month)
    if error:
        raise ValueError(error)
    target = pdf_path.with_name(target_filename(contractor, month))
    if target.exists():
        raise FileExistsError(f"{target.name} already exists")
    pdf_path.rename(target)
    return target


# Czech alphabet order: letters with a háček (č ř š ž) are separate letters
# that sort right after their base letter; other accents (á, é, í, ...) sort
# with the plain letter.
_HACEK = "̌"


def _czech_key(text: str):
    key = []
    for ch in unicodedata.normalize("NFD", text.lower()):
        if unicodedata.combining(ch):
            if ch == _HACEK and key:
                key[-1] = (key[-1][0], 1)
            continue
        key.append((ch, 0))
    return key


def surname_sort_key(name: str):
    """Sort key: surname (last word), then the full name, in Czech order."""
    parts = (name or "").split()
    surname = parts[-1] if parts else ""
    return (_czech_key(surname), _czech_key(name or ""))


def display_name(name: str) -> str:
    """'Émile Dupont' -> 'Dupont Émile' (surname first) for
    display only. Stored names and filenames keep their original order;
    contractor matching ignores word order, so both forms find the same person."""
    parts = (name or "").split()
    if len(parts) < 2:
        return name or ""
    return " ".join([parts[-1], *parts[:-1]])
