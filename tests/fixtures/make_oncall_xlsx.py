"""Writes a small synthetic on-call attendance xlsx with the standard library
(the app reads xlsx the same way). Made-up names only. Layout mirrors the
real sheet: one tab per month, header row 'Date | Name | Name | Standby hrs
(...) | On-call work hrs (...) | Night shift (...) | Ticket report | Notes',
one row per day, GREEN fill = national holiday, orange = weekend."""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

HEADER = ["Date", "Name", "Name", "Standby hrs (17:00 - 22:00)", "On-call work hrs (between 17:00 - 22:00)",
          "Night shift (after 22:00)", "Ticket report", "Notes"]
GREEN, ORANGE = 1, 2  # cellXfs indexes below

_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="1"><font><sz val="10"/></font></fonts>
<fills count="4"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFD9EAD3"/></patternFill></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFFCE5CD"/></patternFill></fill></fills>
<borders count="1"><border/></borders>
<cellXfs count="3"><xf fillId="0"/><xf fillId="2" applyFill="1"/><xf fillId="3" applyFill="1"/></cellXfs>
</styleSheet>"""


def _col(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _cell(ref, value, style):
    s = f' s="{style}"' if style else ""
    if value is None or value == "":
        return f'<c r="{ref}"{s}/>' if style else ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}"{s}><v>{value}</v></c>'
    return f'<c r="{ref}" t="inlineStr"{s}><is><t xml:space="preserve">{escape(str(value))}</t></is></c>'


def write_attendance_xlsx(path: Path, months: dict):
    """months: {"Jan": {"title": "JANUARY", "header": [...] (optional),
    "rows": [(style, [cells...]), ...]} — style is None, GREEN or ORANGE."""
    path = Path(path)
    sheets_xml, sheet_entries, rel_entries = [], [], []
    for n, (name, spec) in enumerate(months.items(), start=1):
        rows_xml = []
        all_rows = [(None, [spec.get("title", name.upper())]), (None, spec.get("header", HEADER))] + list(spec["rows"])
        for r, (style, cells) in enumerate(all_rows, start=1):
            width = max(len(cells), len(HEADER)) if style else len(cells)
            xml_cells = "".join(_cell(f"{_col(i)}{r}", cells[i] if i < len(cells) else None, style) for i in range(width))
            rows_xml.append(f'<row r="{r}">{xml_cells}</row>')
        sheets_xml.append(
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<sheetData>{"".join(rows_xml)}</sheetData></worksheet>'
        )
        sheet_entries.append(f'<sheet name="{escape(name)}" sheetId="{n}" r:id="rId{n}"/>')
        rel_entries.append(
            f'<Relationship Id="rId{n}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{n}.xml"/>'
        )
    n_styles = len(months) + 1
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                   + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                             for i in range(1, len(months) + 1))
                   + "</Types>")
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                   '</Relationships>')
        z.writestr("xl/workbook.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                   f'<sheets>{"".join(sheet_entries)}</sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join(rel_entries)
                   + f'<Relationship Id="rId{n_styles}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                   '</Relationships>')
        z.writestr("xl/styles.xml", _STYLES)
        for i, xml in enumerate(sheets_xml, start=1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", xml)
    return path


def sample_months() -> dict:
    """January: Émile Dupont on call Mon–Fri 5 h standby, with 2 h active work
    on Tuesday and a GREEN holiday (Thursday 1 Jan) with 14 h standby and 1 h
    active work; staff member 'Mia Novak' alongside; weekend rows orange."""
    return {
        "Jan": {"rows": [
            (GREEN, ["01.01.2026", "Novak Mia", "Dupont Émile", 14, "1h", "", "", ""]),
            (None, ["02.01.2026", "Novak Mia", "Dupont Émile", 5, "", "", "", ""]),
            (ORANGE, ["03.01.2026", "", "", "", "", "", "", ""]),
            (ORANGE, ["04.01.2026", "", "", "", "", "", "", ""]),
            (None, ["05.01.2026", "Novak Mia", "Dupont Émile", 5, "", "", "", ""]),
            (None, ["06.01.2026", "Novak Mia", "Dupont Émile", 5, "about 2h", "", "", ""]),
            (None, ["07.01.2026", "Novak Mia", "Dupont Émile", 6, "", "1h", "", ""]),
            (None, ["08.01.2026", "Novak Mia", "Zoe Lindqvist", 5, "", "", "", ""]),
            (None, ["09.01.2026", "Novak Mia", "Zoë Lindqvist", 5, "no idea", "", "", ""]),
        ]},
        "Feb": {"rows": [
            (None, ["02.02.2026", "Dupont Émile", "Zoë Lindqvist", 5, "3 hours", "", "", ""]),
        ]},
    }


if __name__ == "__main__":
    import sys
    write_attendance_xlsx(Path(sys.argv[1]), sample_months())
