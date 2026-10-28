"""Duplicate-invoice detection: file fingerprinting + invoice-number cross-check."""

from __future__ import annotations

import hashlib
from pathlib import Path

from pipeline import db


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def check_duplicate(file_hash: str, contractor_id: int | None, invoice_number: str | None):
    """Returns a warning string if this looks like a duplicate/reissue, else None."""
    # The exact same file (same hash) being validated again is a re-run, not
    # a duplicate: db.upsert_invoice updates it in place. Only a *different*
    # file reusing the same invoice number is suspicious.
    if contractor_id is not None and invoice_number:
        matches = db.find_invoice_by_number(contractor_id, invoice_number)
        others = [m for m in matches if m["file_sha256"] != file_hash]
        if others:
            return (
                f"Invoice number '{invoice_number}' was already used by this contractor "
                f"with a different file — possible reissue or duplicate numbering."
            )
    return None
