"""Local-only PDF field extraction: text layer via pdfplumber, OCR fallback via
Tesseract/pdf2image for scanned PDFs. No network calls, no cloud OCR/LLM APIs.

Every invoice layout may differ, so extraction is deliberately conservative:
when a field can't be found under an unambiguous label, or multiple
candidates conflict, we return low-confidence/None rather than guess — the
caller (pipeline/validate.py) turns that into a Manual Review status.
"""

import calendar
import re
import shutil
import unicodedata
from pathlib import Path

import pdfplumber
from dateutil import parser as dateparser


def normalize(text: str) -> str:
    """Lowercase + strip diacritics, so keyword matching is robust whether a
    PDF's text layer (or OCR) renders accented Czech characters correctly or
    not (e.g. 'vystavení'/'vystaveni' both match)."""
    text = text.lower()
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))

# Homebrew binaries (tesseract, poppler's pdftoppm) may not be on PATH unless
# the user has sourced `brew shellenv` in their shell profile. Rather than
# depend on that, look in Homebrew's standard install locations directly —
# this keeps OCR working regardless of how the app is launched.
_HOMEBREW_BIN_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")


def _find_binary(name: str):
    for bin_dir in _HOMEBREW_BIN_DIRS:
        candidate = Path(bin_dir) / name
        if candidate.exists():
            return str(candidate)
    return shutil.which(name)


def _find_poppler_dir():
    for bin_dir in _HOMEBREW_BIN_DIRS:
        if (Path(bin_dir) / "pdftoppm").exists():
            return bin_dir
    return None  # fall back to pdf2image's default PATH lookup

FILENAME_RE = re.compile(r"^(?P<name>.+?)[_\s]+(?P<year>\d{4})-(?P<month>\d{2})\b")
# Amount with 2 decimals. (?!\d) rather than \b: some PDF text layers glue
# the next word straight onto the number (e.g. "10,000.00Total"), and \b
# fails between a digit and a letter.
AMOUNT_RE = re.compile(r"\d{1,3}(?:[,.\s]\d{3})*(?:[.,]\d{2})(?!\d)")
# Whole amount with thousands grouping and no decimals (e.g. "55 000 USD").
# Only used as a fallback on a total line that has no decimal amount.
WHOLE_AMOUNT_RE = re.compile(r"(?<![\d.,])\d{1,3}(?:[,.\s]\d{3})+(?![\d]|[.,]\d)")
# A line that is nothing but a EUR amount, e.g. "4,000.00 EUR" or "€ 4,000.00" —
# how USD-billed invoices print the EUR equivalent right under the USD total.
EUR_ONLY_LINE_RE = re.compile(
    r"^\s*(\d{1,3}(?:[,.\s]\d{3})*(?:[.,]\d{2})?)\s*(?:eur|€)\s*$"
    r"|^\s*(?:eur|€)\s*(\d{1,3}(?:[,.\s]\d{3})*(?:[.,]\d{2})?)\s*$"
)
MONTH_NAME_RE = re.compile(
    r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})",
    re.IGNORECASE,
)

# Keyword lists are bilingual (English + Czech) since contractors invoice
# in both — confirmed against a real Fakturoid-generated Czech invoice,
# which uses "Datum vystaveni", "Sumar"/"Celkem", "Faktura", etc. instead of
# the English equivalents.
ONCALL_KEYWORDS = ("on call", "on-call", "oncall", "pohotovost")
TOTAL_KEYWORDS = (
    "grand total", "total due", "amount due", "balance due", "total compensation",
    "monthly compensation", "amount payable", "invoice total", "subtotal", "total",
    "sumar", "celkem", "soucet", "k uhrade", "castka k uhrade", "celkova castka",
    "amounts",  # Fakturoid English layout labels its total line this way
)
EUR_CURRENCY_KEYWORDS = ("eur", "€")
USD_CURRENCY_KEYWORDS = ("usd", "us$", "$")
# Ordered longest/most specific first. "issue:" covers layouts where "Date of"
# and "issue: <date>" land on separate text lines. Deliberately NOT included:
# "date of taxable supply"/"duzp" (a different date) and bare "date".
ISSUE_DATE_KEYWORDS = ("date of issue", "issue date", "issued on", "invoice date", "datum vystaveni", "issue:")
DUE_DATE_KEYWORDS = ("due date", "payment due", "due on", "due by", "datum splatnosti")
PAYMENT_DATE_KEYWORDS = ("payment date", "date paid", "datum uhrady", "datum platby")
# Date of taxable supply (Czech DUZP). When present, Rule B checks this date
# rather than the issue date. "date of taxable" also covers variants like
# "Date of taxable transaction".
TAXABLE_SUPPLY_KEYWORDS = (
    "datum uskutecneni zdanitelneho plneni", "datum zdanitelneho plneni", "date of taxable supply",
    "taxable supply date", "date of taxable", "duzp",
)
INVOICE_NUMBER_KEYWORDS = ("invoice number", "invoice no", "invoice #", "inv no", "faktura", "faktura c", "faktura cislo")
SERVICE_PERIOD_KEYWORDS = (
    "service period", "for the month of", "billing period", "service month", "period",
    "fakturacni obdobi", "obdobi",
)

MIN_TEXT_LENGTH_BEFORE_OCR = 20

# Matches a date-shaped substring directly, rather than fuzzy-parsing an
# entire line — PDF column layouts often get flattened into one text line
# (e.g. "Bank Account 2531037063/0800 Issue Date 08. 06. 2026"), and
# dateutil's fuzzy parser can choke entirely on the surrounding noise (an
# account number) even though the date itself is unambiguous.
_MONTH_NAME = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
DATE_PATTERN = re.compile(
    r"\d{4}-\d{1,2}-\d{1,2}"           # ISO: 2026-06-08
    r"|\d{1,2}\.\s*\d{1,2}\.\s*\d{2,4}"  # Czech/European: 08. 06. 2026
    r"|\d{1,2}/\d{1,2}/\d{2,4}"         # 08/06/2026
    r"|\b" + _MONTH_NAME + r"\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}"      # Sep 30, 2026
    r"|\b\d{1,2}(?:st|nd|rd|th)?\.?\s+" + _MONTH_NAME + r"\.?,?\s+\d{4}",  # 1st October 2026
    re.IGNORECASE,
)
# How many following lines to check when a label has no date after it on its
# own line (column layouts: "Invoice number  Issue date" header, values below).
LABEL_LOOKAHEAD_LINES = 2


def _extract_date(line: str):
    m = DATE_PATTERN.search(line)
    if not m:
        return None
    try:
        return dateparser.parse(m.group(0), dayfirst=True).date()
    except (ValueError, OverflowError, TypeError):
        return None


class OcrUnavailable(Exception):
    pass


def parse_amount(raw: str):
    s = raw.strip().replace(" ", "")
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        if re.search(r",\d{2}$", s):
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    try:
        return round(float(s), 2)
    except ValueError:
        return None


def parse_filename(pdf_path: Path):
    m = FILENAME_RE.match(pdf_path.stem)
    if not m:
        return None, None, None
    # NFC so "Dupont" from a filename (macOS may hand back decomposed
    # accents) equals "Dupont" typed in the Contracts tab.
    return unicodedata.normalize("NFC", m.group("name")), int(m.group("year")), int(m.group("month"))


def _ocr_extract(pdf_path: Path) -> str:
    try:
        import pytesseract
        from pdf2image import convert_from_path
    except ImportError as e:
        raise OcrUnavailable(f"OCR libraries not installed: {e}") from e

    tesseract_cmd = _find_binary("tesseract")
    if not tesseract_cmd:
        raise OcrUnavailable("tesseract binary not found (brew install tesseract)")
    pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

    try:
        images = convert_from_path(str(pdf_path), poppler_path=_find_poppler_dir())
        return "\n".join(pytesseract.image_to_string(img) for img in images)
    except Exception as e:  # poppler binary missing, or OCR failure
        raise OcrUnavailable(f"OCR unavailable or failed: {e}") from e


def extract_text(pdf_path: Path):
    """Returns (text, method) where method is 'text' or 'ocr'."""
    with pdfplumber.open(pdf_path) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)

    if len(text.strip()) >= MIN_TEXT_LENGTH_BEFORE_OCR:
        return text, "text"

    try:
        ocr_text = _ocr_extract(pdf_path)
        if len(ocr_text.strip()) >= MIN_TEXT_LENGTH_BEFORE_OCR:
            return ocr_text, "ocr"
    except OcrUnavailable:
        pass

    return text, "text"  # likely near-empty; downstream fields will come back missing -> Manual Review


def _date_for_label(lines, i, keywords, lookahead=0):
    """If line i contains one of keywords, return (date, snippet) for the
    first date AFTER the label on that line — not just anywhere on the line,
    since flattened column layouts put several dates on one line. If the
    label has no date after it, optionally check the next `lookahead` lines
    (label-above-value layouts). Returns (None, None) otherwise."""
    lower = normalize(lines[i])
    for kw in keywords:
        pos = lower.find(kw)
        if pos < 0:
            continue
        rest = lower[pos + len(kw):]
        d = _extract_date(rest)
        if d:
            return d, lines[i].strip()
        if i + 1 < len(lines):
            joined = rest + " " + normalize(lines[i + 1])
            m = DATE_PATTERN.search(joined)
            if m and m.start() < len(rest):  # date must begin on the label's own line
                d = _extract_date(joined)
                if d:
                    return d, f"{lines[i].strip()} {lines[i + 1].strip()}"
        for j in range(i + 1, min(i + 1 + lookahead, len(lines))):
            d = _extract_date(normalize(lines[j]))
            if d:
                return d, f"{lines[i].strip()} / {lines[j].strip()}"
        return None, None
    return None, None


def find_date_by_keywords(lines, keywords):
    for i in range(len(lines)):
        d, snippet = _date_for_label(lines, i, keywords)
        if d:
            return d, snippet
    return None, None


def find_issue_date(lines):
    """Returns (date_or_None, snippet_or_None, confidence) where confidence
    is 'high', 'missing', or 'ambiguous' (multiple conflicting candidates)."""
    matches = []
    for i in range(len(lines)):
        d, snippet = _date_for_label(lines, i, ISSUE_DATE_KEYWORDS, lookahead=LABEL_LOOKAHEAD_LINES)
        if d:
            matches.append((d, snippet))
    distinct_dates = {d for d, _ in matches}
    if not distinct_dates:
        return None, None, "missing"
    if len(distinct_dates) > 1:
        return None, "; ".join(l for _, l in matches), "ambiguous"
    return matches[0][0], matches[0][1], "high"


def find_taxable_supply_date(lines):
    """Returns (date_or_None, snippet_or_None, confidence) — 'high',
    'missing' (no such label on the invoice) or 'ambiguous'."""
    matches = []
    for i in range(len(lines)):
        d, snippet = _date_for_label(lines, i, TAXABLE_SUPPLY_KEYWORDS)
        if d:
            matches.append((d, snippet))
    distinct = {d for d, _ in matches}
    if not distinct:
        return None, None, "missing"
    if len(distinct) > 1:
        return None, "; ".join(s for _, s in matches), "ambiguous"
    return matches[0][0], matches[0][1], "high"


def _strip_accents_keep_case(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


# An invoice ID is one token (letters, digits, - / .). Callers additionally
# require it to contain a digit, so words like "date" or "generated" after
# "Invoice" are never taken.
_INVOICE_ID = r"([A-Za-z0-9][\w\-/.]*[A-Za-z0-9]|[A-Za-z0-9])"
_INVOICE_LABEL_RE = re.compile(
    r"\b(?:invoice\s*(?:number|no\.?|nr\.?|#)?|faktura(?:\s*c(?:islo)?\.?)?|cislo\s+faktury|inv\.?\s*no\.?)"
    r"\s*[:#]?\s*" + _INVOICE_ID + r"(?=\s|$)",
    re.IGNORECASE,
)
_HEADER_LABEL_RE = re.compile(r"\b(invoice\s*(number|no\.?)|cislo\s+faktury)\b", re.IGNORECASE)
_LINE_START_ID_RE = re.compile(r"^\s*" + _INVOICE_ID + r"(?=\s|$)")


def find_invoice_number(lines):
    """Returns (invoice_number, snippet). Handles 'Invoice 2026-0009',
    'Invoice no.: FV123', 'Faktura 2026009', and header layouts where the
    label row ('Invoice number  Issue date') has the value on a line below."""
    for i, line in enumerate(lines):
        plain = _strip_accents_keep_case(line)
        pos = 0
        while (m := _INVOICE_LABEL_RE.search(plain, pos)):
            if any(ch.isdigit() for ch in m.group(1)):
                return m.group(1), line.strip()
            pos = m.start() + 1  # e.g. "Invoice Invoice no: 123" — retry from the next label
        if _HEADER_LABEL_RE.search(plain):
            for j in range(i + 1, min(i + 1 + LABEL_LOOKAHEAD_LINES, len(lines))):
                m2 = _LINE_START_ID_RE.match(_strip_accents_keep_case(lines[j]))
                if m2 and any(ch.isdigit() for ch in m2.group(1)):
                    return m2.group(1), f"{line.strip()} / {lines[j].strip()}"
    return None, None

def find_service_month_text(lines):
    for line in lines:
        lower = normalize(line)
        if any(kw in lower for kw in SERVICE_PERIOD_KEYWORDS):
            m = MONTH_NAME_RE.search(line)
            if m:
                month_name, year = m.groups()
                month_num = list(calendar.month_name).index(month_name.capitalize())
                return (int(year), month_num), line.strip()
            m2 = re.search(r"(\d{4})-(\d{2})", line)
            if m2:
                return (int(m2.group(1)), int(m2.group(2))), line.strip()
    return None, None


def find_currency(lines, text):
    for line in lines:
        m = re.search(r"currency\s*[:\-]?\s*([A-Za-z]{3})", line, re.IGNORECASE)
        if m:
            return m.group(1).upper()
    normalized_text = normalize(text)
    if any(k in normalized_text for k in ("eur", "€")):
        return "EUR"
    if "usd" in normalized_text:
        return "USD"
    return None


def find_oncall(lines):
    for line in lines:
        if any(kw in normalize(line) for kw in ONCALL_KEYWORDS):
            return True, line.strip()
    return False, None


def find_oncall_amount(lines):
    """The amount on an On Call line of its own (not a total line), e.g.
    "On Call Fee EUR: 10,000.00" -> (10000.0, 'EUR'). Returns (None, None)
    when no such line carries an amount."""
    for line in lines:
        lower = normalize(line)
        if not any(kw in lower for kw in ONCALL_KEYWORDS):
            continue
        if any(kw in lower for kw in TOTAL_KEYWORDS):
            continue  # a grand total that merely mentions On Call: see find_combined_total
        amounts = AMOUNT_RE.findall(line)
        value = parse_amount(amounts[-1]) if amounts else None
        if value is None:
            whole = WHOLE_AMOUNT_RE.findall(line)
            if whole:
                value = float(re.sub(r"\D", "", whole[-1]))
        if value is None:
            continue
        if any(kw in lower for kw in USD_CURRENCY_KEYWORDS):
            return value, "USD"
        if any(kw in lower for kw in EUR_CURRENCY_KEYWORDS):
            return value, "EUR"
        return value, None
    return None, None


def find_combined_total(lines, currency_keywords):
    """The figure on a total line that includes On Call, e.g.
    "Grand Total (incl. On Call) EUR: 110,000.00" -> 110000.0, else None."""
    value = None
    for line in lines:
        lower = normalize(line)
        if not (any(kw in lower for kw in TOTAL_KEYWORDS) and any(kw in lower for kw in currency_keywords)
                and any(kw in lower for kw in ONCALL_KEYWORDS)):
            continue
        amounts = AMOUNT_RE.findall(line)
        if amounts:
            value = parse_amount(amounts[-1]) or value
        else:
            whole = WHOLE_AMOUNT_RE.findall(line)
            if whole:
                value = float(re.sub(r"\D", "", whole[-1]))
    return value


def find_amount(lines, currency_keywords):
    """Finds a TOTAL_KEYWORDS line that also matches currency_keywords,
    excluding lines that are themselves On Call lines. Returns
    (value, confidence, snippet) where confidence is 'high'/'medium'/'missing'/'ambiguous'.
    'ambiguous' means a total-like line exists but it's combined with an On
    Call amount and can't be reliably split — never guessed."""
    candidates = []
    oncall_total_combo = False
    for line in lines:
        lower = normalize(line)
        has_total_kw = any(kw in lower for kw in TOTAL_KEYWORDS)
        has_currency_kw = any(kw in lower for kw in currency_keywords)
        has_oncall_kw = any(kw in lower for kw in ONCALL_KEYWORDS)
        if has_total_kw and has_currency_kw:
            amounts = AMOUNT_RE.findall(line)
            if amounts:
                value = parse_amount(amounts[-1])
            else:
                whole = WHOLE_AMOUNT_RE.findall(line)
                if not whole:
                    continue
                value = float(re.sub(r"\D", "", whole[-1]))
            if value is None:
                continue
            if has_oncall_kw:
                oncall_total_combo = True
                continue
            candidates.append((line.strip(), value))

    if not candidates:
        return None, ("ambiguous" if oncall_total_combo else "missing"), None

    snippet, value = candidates[-1]
    confidence = "high" if len(candidates) == 1 else "medium"
    return value, confidence, snippet


def find_eur_equivalent(lines, usd_snippet):
    """USD-billed invoices often print the EUR equivalent of the total on its
    own line directly under the USD total (e.g. "Total due: $ 4,340.00"
    then "4,000.00 EUR"). Returns (value, confidence, snippet) — only
    'high' when that exact shape is present, otherwise 'missing'."""
    if not usd_snippet:
        return None, "missing", None
    idxs = [i for i, l in enumerate(lines) if l.strip() == usd_snippet]
    if not idxs:
        return None, "missing", None
    i = idxs[-1]
    if i + 1 < len(lines):
        m = EUR_ONLY_LINE_RE.match(normalize(lines[i + 1]))
        if m:
            value = parse_amount(m.group(1) or m.group(2))
            if value is not None:
                return value, "high", f"{usd_snippet} / {lines[i + 1].strip()}"
    return None, "missing", None


# "$ 4,000.00" / "4,000.00 USD" alone on a line (a word may be glued on the end).
_USD_ONLY_LINE_RE = re.compile(
    r"^\s*(?:\$|usd)\s*(\d{1,3}(?:[,.\s]\d{3})*[.,]\d{2})[a-z]*\s*$"
    r"|^\s*(\d{1,3}(?:[,.\s]\d{3})*[.,]\d{2})\s*(?:\$|usd)\s*$"
)
_STATED_RATE_RE = re.compile(r"(\d[.,]\d{2,6})\s*(?:usd|\$)\s*/\s*1\s*(?:eur|€)")


def find_unlabelled_total_block(lines):
    """Some layouts print the totals block with the label on a different
    text line from the figure: "$ 4,340.00" then "4,000.00 EUR" then
    "(Exchange rate 1.0850 USD / 1 EUR)". Accepted only when the invoice's
    own stated rate converts that EUR figure into that USD figure (within
    1 USD), so an unrelated pair of numbers can't be picked up by accident.
    Returns (usd, eur, snippet) or (None, None, None)."""
    for i in range(1, len(lines)):
        eur_m = EUR_ONLY_LINE_RE.match(normalize(lines[i]))
        usd_m = _USD_ONLY_LINE_RE.match(normalize(lines[i - 1]))
        if not (eur_m and usd_m):
            continue
        usd = parse_amount(usd_m.group(1) or usd_m.group(2))
        eur = parse_amount(eur_m.group(1) or eur_m.group(2))
        for j in range(i + 1, min(i + 3, len(lines))):
            rate_m = _STATED_RATE_RE.search(normalize(lines[j]))
            if rate_m and usd and eur:
                rate = float(rate_m.group(1).replace(",", "."))
                if abs(eur * rate - usd) <= 1.0:
                    return usd, eur, f"{lines[i - 1].strip()} / {lines[i].strip()} / {lines[j].strip()}"
    return None, None, None


def extract_invoice_fields(pdf_path: Path) -> dict:
    name, year, month = parse_filename(pdf_path)
    text, method = extract_text(pdf_path)
    lines = text.splitlines()

    invoice_number, _ = find_invoice_number(lines)
    issue_date, issue_snippet, issue_confidence = find_issue_date(lines)
    due_date, _ = find_date_by_keywords(lines, DUE_DATE_KEYWORDS)
    payment_date, _ = find_date_by_keywords(lines, PAYMENT_DATE_KEYWORDS)
    supply_date, supply_snippet, supply_confidence = find_taxable_supply_date(lines)
    currency = find_currency(lines, text)
    service_month_text, _ = find_service_month_text(lines)
    oncall_present, oncall_snippet = find_oncall(lines)
    oncall_amount_value, oncall_amount_currency = find_oncall_amount(lines)
    combined_eur_value = find_combined_total(lines, EUR_CURRENCY_KEYWORDS)
    combined_usd_value = find_combined_total(lines, USD_CURRENCY_KEYWORDS)
    eur_value, eur_confidence, eur_snippet = find_amount(lines, EUR_CURRENCY_KEYWORDS)
    eur_source = "invoice_line" if eur_value is not None else None
    usd_value, usd_confidence, usd_snippet = find_amount(lines, USD_CURRENCY_KEYWORDS)
    if eur_confidence == "missing" and usd_confidence in ("high", "medium"):
        eur_value, eur_confidence, eur_snippet = find_eur_equivalent(lines, usd_snippet)
        eur_source = "usd_equivalent" if eur_value is not None else None
    if eur_confidence == "missing" and usd_confidence == "missing":
        block_usd, block_eur, block_snippet = find_unlabelled_total_block(lines)
        if block_usd is not None:
            usd_value, usd_confidence, usd_snippet = block_usd, "high", block_snippet
            eur_value, eur_confidence, eur_snippet = block_eur, "high", block_snippet
            eur_source = "usd_equivalent"

    service_month = f"{year:04d}-{month:02d}" if year else None
    service_month_source = "filename" if service_month else None
    if service_month_text:
        text_month_str = f"{service_month_text[0]:04d}-{service_month_text[1]:02d}"
        if service_month and text_month_str != service_month:
            service_month_source = "conflict"
        elif not service_month:
            service_month = text_month_str
            service_month_source = "text"

    return {
        "contractor_name": name,
        "service_month": service_month,
        "service_month_source": service_month_source,
        "invoice_number": invoice_number,
        "issue_date": issue_date.isoformat() if issue_date else None,
        "issue_date_confidence": issue_confidence,
        "issue_date_snippet": issue_snippet,
        "due_date": due_date.isoformat() if due_date else None,
        "payment_date": payment_date.isoformat() if payment_date else None,
        "taxable_supply_date": supply_date.isoformat() if supply_date else None,
        "taxable_supply_confidence": supply_confidence,
        "taxable_supply_snippet": supply_snippet,
        "currency": currency,
        "standard_amount_value": eur_value,
        # 'usd_equivalent' = the EUR figure printed under a USD total, i.e.
        # USD (whole cents) x rate — only precise to about one cent's worth of EUR.
        "standard_amount_source": eur_source,
        "standard_amount_confidence": eur_confidence,
        "standard_amount_snippet": eur_snippet,
        "usd_amount_value": usd_value,
        "usd_amount_confidence": usd_confidence,
        "usd_amount_snippet": usd_snippet,
        "oncall_present": oncall_present,
        "oncall_snippet": oncall_snippet,
        # The On Call line's own amount (when it is a separate line) and the
        # figure of a total line that already includes On Call — used by the
        # on-call rule in validate.py; not stored in the DB.
        "oncall_amount_value": oncall_amount_value,
        "oncall_amount_currency": oncall_amount_currency,
        "combined_eur_value": combined_eur_value,
        "combined_usd_value": combined_usd_value,
        "extraction_method": method,
    }
