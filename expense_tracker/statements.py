"""Bank account statements (HDFC-style .xls/.xlsx or comma-delimited .txt/.csv) → expense records.

Only withdrawals become expenses; deposits are counted and skipped. Imported rows get
no Label (category ""), so the user can set one later.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from expense_tracker import ExpenseTrackerError

# Field → accepted header spellings after header_key(); the .txt export truncates
# some headers ("Value Dat"), so matching is by prefix.
HEADERS = {
    "date": ("date",),
    "narration": ("narration", "description", "particulars"),
    "ref": ("chqrefno", "chqrefnumber", "refno", "chequeno"),
    "value_date": ("valuedt", "valuedat", "valuedate"),
    "debit": ("withdrawalamt", "debitamount", "withdrawal", "debit"),
    "credit": ("depositamt", "creditamount", "deposit", "credit"),
    "balance": ("closingbalance", "balance"),
}
REQUIRED = ("date", "narration", "debit", "credit")
DATE_FORMATS = ("%d/%m/%y", "%d/%m/%Y", "%d-%m-%Y", "%d-%m-%y", "%Y-%m-%d")

# Narration prefix → Payment shown in the dashboard; the first match wins.
CHANNELS = (
    (re.compile(r"UPI"), "UPI"),
    (re.compile(r"(POS|ME DC|DC |CRV POS)"), "Card"),
    (re.compile(r"ACH"), "ACH"),
    (re.compile(r"NEFT"), "NEFT"),
    (re.compile(r"IMPS"), "IMPS"),
    (re.compile(r"RTGS"), "RTGS"),
    (re.compile(r"(ATW|NWD|EAW)"), "ATM"),
)


def header_key(value) -> str:
    return re.sub(r"[^a-z]", "", str(value).lower())


def read_table(path: Path) -> list[list]:
    """All cells of the statement's first sheet (or delimited file) as rows of values."""
    suffix = path.suffix.lower()
    if suffix == ".xls":
        try:
            import xlrd
        except ImportError:
            raise ExpenseTrackerError("Reading .xls statements needs xlrd: pip install -r requirements.txt")
        sheet = xlrd.open_workbook(str(path)).sheet_by_index(0)
        return [sheet.row_values(i) for i in range(sheet.nrows)]
    if suffix in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=True, data_only=True)
        try:
            return [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]
        finally:
            wb.close()
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
        return [row for row in csv.reader(f)]


def find_columns(rows: list[list]) -> tuple[int, dict[str, int]]:
    """Index of the transaction header row and the column of each known field."""
    for i, row in enumerate(rows):
        keys = [header_key(c) for c in row]
        cols = {}
        for field, names in HEADERS.items():
            for j, key in enumerate(keys):
                if key and j not in cols.values() and any(key.startswith(n) for n in names):
                    cols[field] = j
                    break
        if all(f in cols for f in REQUIRED):
            return i, cols
    raise ExpenseTrackerError(
        "Couldn't find the transaction header (Date, Narration, Withdrawal/Debit, Deposit/Credit) "
        "in this statement.")


def parse_date(value) -> date | None:
    text = str(value or "").strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def parse_amount(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").replace(",", "").strip()
    return float(text) if text else 0.0


def channel(narration: str) -> str:
    return next((name for rx, name in CHANNELS if rx.match(narration)), "Bank")


def read_statement(path: Path) -> tuple[list[dict], set[tuple]]:
    """Return (withdrawal records, identities of the deposits skipped) from one statement file."""
    if not path.exists():
        raise ExpenseTrackerError(f"Statement not found: {path}")
    rows = read_table(path)
    header, cols = find_columns(rows)
    cell = lambda r, f: r[cols[f]] if f in cols and cols[f] < len(r) else ""
    out, deposits = [], set()
    for r in rows[header + 1:]:
        d = parse_date(cell(r, "date"))
        if d is None:  # separator lines, blank lines, the summary block
            continue
        try:
            debit, credit = parse_amount(cell(r, "debit")), parse_amount(cell(r, "credit"))
            balance = parse_amount(cell(r, "balance"))
        except ValueError:
            raise ExpenseTrackerError(f"{path.name}: unreadable amount in row {r}")
        ref = str(cell(r, "ref")).strip()
        if debit <= 0:
            if credit > 0:
                deposits.add((d, ref, round(credit, 2), round(balance, 2)))
            continue
        narration = " ".join(str(cell(r, "narration")).split())
        value_date = parse_date(cell(r, "value_date"))
        extra = {"Ref": ref, "Value date": value_date.isoformat() if value_date else "",
                 "Closing balance": f"{balance:.2f}"}
        out.append({
            "date": d.isoformat(),
            "month": d.strftime("%Y-%m"),
            "year": d.year,
            "amount": round(debit, 2),
            "category": "",
            "subcategory": None,
            "description": narration or None,
            "payment": channel(narration),
            "extra": json.dumps({k: v for k, v in extra.items() if v}, ensure_ascii=False),
            "origin": "bank",
            "_balance": round(balance, 2),
            "_ref": ref,
        })
    return out, deposits


def assign_row_keys(records: list[dict]) -> None:
    """Identity = date, reference, amount and closing balance (+ occurrence), which are the
    same in every export format and in overlapping statements, unlike narration padding."""
    seen = Counter()
    for rec in records:
        base = f"bank|{rec['date']}|{rec['_ref']}|{rec['amount']:.2f}|{rec['_balance']:.2f}"
        seen[base] += 1
        rec["row_key"] = hashlib.sha1(f"{base}#{seen[base]}".encode()).hexdigest()


def read_statements(paths: list[Path]) -> tuple[list[dict], int]:
    """Records from several statements, with the same transaction in two files kept once."""
    records, deposits = {}, set()
    for path in paths:
        recs, dep = read_statement(path)
        assign_row_keys(recs)
        for rec in recs:
            records.setdefault(rec["row_key"], rec)
        deposits |= dep
    out = sorted(records.values(), key=lambda r: r["date"])
    for rec in out:
        del rec["_balance"], rec["_ref"]
    return out, len(deposits)
