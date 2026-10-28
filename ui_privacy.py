"""Privacy mode for screen sharing: contractor names become stable aliases
("Contractor A", "Contractor B", … in surname order), amounts, invoice
numbers and text snippets are masked. Display-only — nothing stored changes."""

from __future__ import annotations

import re
import string

from pipeline.naming import display_name, surname_sort_key

MASK = "•••••"


def _letters(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = string.ascii_uppercase[r] + s
    return s


class View:
    def __init__(self, private: bool, contractor_names):
        self.private = private
        ordered = sorted(set(contractor_names), key=surname_sort_key)
        self._alias = {n: f"Contractor {_letters(i)}" for i, n in enumerate(ordered)}

    def who(self, name):
        if not name:
            return "—"
        if self.private:
            return self._alias.get(name, "Contractor ?")
        return display_name(name)

    def amount(self, value, fmt="{:,.2f}"):
        if value is None:
            return "—"
        return MASK if self.private else fmt.format(value)

    def text(self, value):
        """Free text (warnings, invoice numbers): digits masked in privacy mode."""
        if value is None or value == "":
            return "—" if value is None else ""
        return re.sub(r"\d", "•", str(value)) if self.private else str(value)

    def snippet(self, value):
        """Raw text copied from an invoice: hidden entirely in privacy mode."""
        if value is None:
            return None
        return MASK if self.private else value
