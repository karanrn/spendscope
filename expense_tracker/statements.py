"""Bank account statements (.xls/.xlsx, HTML saved as .xls, or delimited .txt/.csv) → expense records.

The transaction header row is found automatically: each header cell is matched against
common bank spellings ("Txn Date", "Narration", "Withdrawal Amt.", "DR", "Amount" with a
"Dr/Cr" column, ...). [statements.columns] in config.toml can name headers that aren't
recognised. Only withdrawals become expenses; deposits are counted and skipped. Imported
rows get no Label (category ""), so the user can set one later.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path

from expense_tracker import ExpenseTrackerError

# Field → pattern for a header cell after header_key() (lower-case letters only). A cell
# belongs to the first field, in this order, whose pattern it matches; each field takes
# its first matching cell. The order matters: "Value Date" is not the date, and
# "Withdrawal Amount" is a debit, not a signed amount.
FIELD_PATTERNS = (
    ("value_date", r"^value"),
    ("date", r"date|^(txn|tran|trans|transaction|posting|post|book)?dt$"),
    ("balance", r"balance|^bal$|^closingbal"),
    ("type", r"^(drcr|crdr|drcrindicator|debitcredit|creditdebit|type|txntype|trantype|transactiontype)$"),
    ("debit", r"debit|withdraw|^(dr|dramt|dramount|paidout|moneyout)$"),
    ("credit", r"credit|deposit|^(cr|cramt|cramount|paidin|moneyin)$"),
    ("amount", r"^amount|amount$|^amt$|amountinr"),
    ("ref", r"ref|cheque|chq|utr"),
    ("narration", r"narration|description|particular|remark|details|^memo$|^payee"),
)
FIELDS = [f for f, _ in FIELD_PATTERNS]

DATE_FORMATS = ("%d/%m/%y", "%d/%m/%Y", "%d-%m-%Y", "%d-%m-%y", "%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d",
                "%d %b %Y", "%d %b %y", "%d-%b-%Y", "%d-%b-%y", "%d/%b/%Y", "%d/%b/%y",
                "%d %B %Y", "%d-%B-%Y", "%b %d, %Y")
DELIMITERS = (",", "\t", ";", "|")
TIME_SUFFIX = re.compile(r"[\sT]+\d{1,2}:\d{2}(:\d{2}(\.\d+)?)?(\s*[AP]M)?$", re.I)

# Narration text → Payment shown in the dashboard; the first match wins.
CHANNELS = (
    (re.compile(r"\bUPI", re.I), "UPI"),
    (re.compile(r"\bNEFT", re.I), "NEFT"),
    (re.compile(r"\bIMPS", re.I), "IMPS"),
    (re.compile(r"\bRTGS", re.I), "RTGS"),
    (re.compile(r"\b(N?ACH|ECS)\b", re.I), "ACH"),
    (re.compile(r"\b(ATM|ATW|NWD|EAW|CASH ?WDL)", re.I), "ATM"),
    (re.compile(r"\b(POS|ME DC|DC|DEBIT CARD|VPS|IPS)\b", re.I), "Card"),
)


def header_key(value) -> str:
    return re.sub(r"[^a-z]", "", str(value).lower())


def as_list(value) -> list[str]:
    return [value] if isinstance(value, str) else list(value or [])


class _TableParser(HTMLParser):
    """Rows of the HTML tables that some banks save with an .xls name."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.rows.append([])
        elif tag in ("td", "th"):
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            if not self.rows:
                self.rows.append([])
            self.rows[-1].append(" ".join("".join(self._cell).split()))
            self._cell = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def read_tables(path: Path) -> list[list[list]]:
    """The statement's first sheet (or HTML table) as rows of values. A delimited text file
    gives one candidate table per delimiter; the one with a header row is used."""
    head = path.read_bytes()[:2048].lstrip().lower()
    if head.startswith((b"<html", b"<!doctype html", b"<table", b"<?xml")) or b"<table" in head:
        parser = _TableParser()
        parser.feed(path.read_text(encoding="utf-8", errors="replace"))
        return [parser.rows]
    suffix = path.suffix.lower()
    if suffix == ".xls":
        try:
            import xlrd
        except ImportError:
            raise ExpenseTrackerError("Reading .xls statements needs xlrd: pip install -r requirements.txt")
        try:
            book = xlrd.open_workbook(str(path))
        except xlrd.XLRDError as exc:
            raise ExpenseTrackerError(f"{path.name}: not a readable .xls file ({exc})")
        sheet = book.sheet_by_index(0)
        return [[[xlrd.xldate_as_datetime(c.value, book.datemode) if c.ctype == xlrd.XL_CELL_DATE else c.value
                  for c in sheet.row(i)] for i in range(sheet.nrows)]]
    if suffix in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=True, data_only=True)
        try:
            return [[list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]]
        finally:
            wb.close()
    lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    return [list(csv.reader(lines, delimiter=d)) for d in DELIMITERS if any(d in line for line in lines)]


def match_columns(row: list, overrides: dict[str, list[str]]) -> dict[str, int]:
    """Column of each field found in this row: configured header names first, then patterns."""
    keys = [header_key(c) if isinstance(c, str) else "" for c in row]
    cols: dict[str, int] = {}
    for field, names in overrides.items():
        wanted = {header_key(n) for n in names if header_key(n)}
        j = next((j for j, k in enumerate(keys) if k in wanted and j not in cols.values()), None)
        if j is not None:
            cols[field] = j
    for j, key in enumerate(keys):
        if not key or j in cols.values():
            continue
        field = next((f for f, rx in FIELD_PATTERNS if re.search(rx, key)), None)
        if field and field not in cols:
            cols[field] = j
    return cols


def usable(cols: dict[str, int]) -> bool:
    return ("date" in cols and "narration" in cols
            and (("debit" in cols and "credit" in cols) or "amount" in cols))


def find_columns(tables: list[list[list]], overrides: dict[str, list[str]],
                 name: str) -> tuple[list[list], int, dict[str, int]]:
    """The table holding transactions, the index of its header row, and the column of each field."""
    best = (-1, [], {})  # (fields recognised, row, columns) of the closest miss
    for rows in tables:
        for i, row in enumerate(rows):
            cols = match_columns(row, overrides)
            if usable(cols):
                return rows, i, cols
            cells = [str(c).strip() for c in row if str(c or "").strip()]
            if cells and len(cols) > best[0]:
                best = (len(cols), cells, cols)
    _, cells, cols = best
    seen = f"\nClosest row: {cells}" if cells else ""
    found = f" (recognised: {', '.join(sorted(cols))})" if cols else ""
    raise ExpenseTrackerError(
        f"{name}: couldn't find the transaction header. It needs a date, a narration/description,"
        f" and debit + credit columns or a single amount column.{seen}{found}\n"
        "Name the headers under [statements.columns] in config.toml.")


def parse_date(value, formats: tuple[str, ...]) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = TIME_SUFFIX.sub("", " ".join(str(value or "").split()))
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def parse_amount(value) -> tuple[float, str]:
    """(amount, "dr"/"cr" marker if the cell carries one). "(1,200.00)" and "-1200" are negative."""
    if isinstance(value, (int, float)):
        return float(value), ""
    text = str(value or "").strip()
    marker = re.search(r"\b(dr|cr)\.?\)?$", text, re.I)
    if marker:
        text = text[:marker.start()].strip()
    kind = marker.group(1).lower() if marker else ""
    negative = text.startswith("(") and text.endswith(")")
    digits = re.sub(r"[^\d.\-]", "", text)
    if digits in ("", "-", ".", "-."):
        return 0.0, kind
    amount = float(digits)  # ValueError for nonsense like "1.2.3"
    return (-abs(amount) if negative else amount), kind


def split_amount(r: list, cell, cols: dict[str, int]) -> tuple[float, float]:
    """(withdrawal, deposit) of one row, from debit/credit columns or a single amount column."""
    if "debit" in cols and "credit" in cols:
        return abs(parse_amount(cell(r, "debit"))[0]), abs(parse_amount(cell(r, "credit"))[0])
    amount, marker = parse_amount(cell(r, "amount"))
    kind = header_key(cell(r, "type")) or marker
    if kind.startswith(("d", "w")):
        return abs(amount), 0.0
    if kind.startswith("c"):
        return 0.0, abs(amount)
    return (-amount, 0.0) if amount < 0 else (0.0, amount)  # signed: negative means money out


def channel(narration: str) -> str:
    return next((name for rx, name in CHANNELS if rx.search(narration)), "Bank")


def statement_options(cfg: dict | None) -> tuple[dict[str, list[str]], tuple[str, ...]]:
    """Header-name overrides and date formats from config.toml's [statements] section."""
    section = (cfg or {}).get("statements", {})
    columns = {f: as_list(v) for f, v in section.get("columns", {}).items() if as_list(v)}
    unknown = set(columns) - set(FIELDS)
    if unknown:
        raise ExpenseTrackerError(f"Unknown [statements.columns] keys: {', '.join(sorted(unknown))}. "
                                  f"Use: {', '.join(FIELDS)}")
    return columns, tuple(section.get("date_formats", ())) + DATE_FORMATS


def read_statement(path: Path, cfg: dict | None = None) -> tuple[list[dict], set[tuple]]:
    """Return (withdrawal records, identities of the deposits skipped) from one statement file."""
    if not path.exists():
        raise ExpenseTrackerError(f"Statement not found: {path}")
    overrides, formats = statement_options(cfg)
    rows, header, cols = find_columns(read_tables(path), overrides, path.name)

    def cell(r, f):
        return r[cols[f]] if f in cols and cols[f] < len(r) and r[cols[f]] is not None else ""

    out, deposits = [], set()
    previous = None  # the last withdrawal, while following rows may continue its narration
    for r in rows[header + 1:]:
        d = parse_date(cell(r, "date"), formats)
        if d is None:
            # Some banks wrap long narrations onto rows with no date or amounts.
            text = " ".join(str(cell(r, "narration")).split())
            money = any(str(cell(r, f)).strip() for f in ("debit", "credit", "amount", "balance"))
            if previous is not None and text and not money and not str(cell(r, "date")).strip():
                previous["description"] = f"{previous['description'] or ''} {text}".strip()
                previous["payment"] = channel(previous["description"])
            else:  # separator lines, blank lines, the summary block
                previous = None
            continue
        previous = None
        try:
            debit, credit = split_amount(r, cell, cols)
            balance = parse_amount(cell(r, "balance"))[0]
        except ValueError:
            raise ExpenseTrackerError(f"{path.name}: unreadable amount in row {r}")
        ref = str(cell(r, "ref")).strip()
        if debit <= 0:
            if credit > 0:
                deposits.add((d, ref, round(credit, 2), round(balance, 2)))
            continue
        narration = " ".join(str(cell(r, "narration")).split())
        value_date = parse_date(cell(r, "value_date"), formats)
        previous = {
            "date": d.isoformat(),
            "month": d.strftime("%Y-%m"),
            "year": d.year,
            "amount": round(debit, 2),
            "category": "",
            "subcategory": None,
            "description": narration or None,
            "payment": channel(narration),
            "extra": {"Ref": ref, "Value date": value_date.isoformat() if value_date else "",
                      "Closing balance": f"{balance:.2f}" if "balance" in cols else ""},
            "origin": "bank",
            "_balance": round(balance, 2),
            "_ref": ref,
        }
        out.append(previous)
    for rec in out:
        rec["extra"] = json.dumps({k: v for k, v in rec["extra"].items() if v}, ensure_ascii=False)
    return out, deposits


def assign_row_keys(records: list[dict]) -> None:
    """Identity = date, reference, amount and closing balance (+ occurrence), which are the
    same in every export format and in overlapping statements, unlike narration padding."""
    seen = Counter()
    for rec in records:
        base = f"bank|{rec['date']}|{rec['_ref']}|{rec['amount']:.2f}|{rec['_balance']:.2f}"
        seen[base] += 1
        rec["row_key"] = hashlib.sha1(f"{base}#{seen[base]}".encode()).hexdigest()


def read_statements(paths: list[Path], cfg: dict | None = None) -> tuple[list[dict], int]:
    """Records from several statements, with the same transaction in two files kept once."""
    records, deposits = {}, set()
    for path in paths:
        recs, dep = read_statement(path, cfg)
        assign_row_keys(recs)
        for rec in recs:
            records.setdefault(rec["row_key"], rec)
        deposits |= dep
    out = sorted(records.values(), key=lambda r: r["date"])
    for rec in out:
        del rec["_balance"], rec["_ref"]
    return out, len(deposits)
