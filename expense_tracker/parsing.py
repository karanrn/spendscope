"""Turn raw spreadsheet rows into expense records."""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import date, datetime, timedelta

from expense_tracker import ExpenseTrackerError

TOTAL_RE = re.compile(r"(?i)^(grand\s*|sub\s*)?totals?:?$")
FIELDS = ["date", "amount", "category", "description", "payment", "subcategory"]


def norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def header_key(value: str) -> str:
    """Normalize optional units such as INR in spreadsheet column headings."""
    return re.sub(r"\s*\([^)]*\)", "", norm(value)).strip().lower()


CURRENCY_CODES = r"rs\.?|inr|usd|eur|gbp|aud|cad|sgd|aed|nzd|chf|jpy"
CURRENCY_SIGNS = "₹$€£¥"


def parse_amount(v, symbol: str, decimal: str = ".", code: str = "") -> float | None:
    """A spreadsheet amount as a number. Currency signs and codes are ignored, "(200)" is
    negative, and decimal="," reads European amounts such as "1.234,56"."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    s = norm(v)
    if not s:
        return None
    neg = s.startswith("(") and s.endswith(")")
    codes = CURRENCY_CODES + (f"|{re.escape(code)}" if code else "")
    s = re.sub(rf"(?i)\b({codes})(?![a-z])", "", s)
    for sign in {symbol, *CURRENCY_SIGNS}:
        s = s.replace(sign, "") if sign else s
    s = s.replace("(", "").replace(")", "")
    if decimal == ",":
        s = re.sub(r"[.\s\u00a0\u202f']", "", s).replace(",", ".")
    else:
        s = re.sub(r"[,\s\u00a0\u202f']", "", s)
    try:
        x = float(s)
    except ValueError:
        return None
    return -x if neg else x


def parse_date(v, formats: list[str]) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, (int, float)) and 20000 < v < 80000:   # spreadsheet serial number
        return date(1899, 12, 30) + timedelta(days=int(v))
    s = norm(v)
    if not s:
        return None
    s = s.split(" ")[0] if re.match(r"^\d{4}-\d{2}-\d{2} \d", s) else s
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def find_headers(rows: list[list], date_col: str, amount_col: str) -> list[int]:
    want = {header_key(date_col), header_key(amount_col)}
    headers = [i for i, row in enumerate(rows)
               if want <= {header_key(norm(c)) for c in row}]
    if headers:
        return headers
    raise ExpenseTrackerError(
        f"Couldn't find a header row with columns '{date_col}' and '{amount_col}'.\n"
        f"First row seen: {rows[0] if rows else '(empty sheet)'}\n"
        "Fix the names under [columns] in config.toml.")


def to_records(rows: list[list], cfg: dict) -> tuple[list[dict], int, list[dict]]:
    """Return (records, rows skipped, rows with content that failed to parse)."""
    cols, parsing = cfg["columns"], cfg.get("parsing", {})
    formats = parsing.get("date_formats", ["%d/%m/%Y", "%Y-%m-%d"])
    symbol = parsing.get("currency_symbol", "₹")
    decimal, code = parsing.get("decimal_separator", "."), parsing.get("currency_code", "")

    headers = find_headers(rows, cols["date"], cols["amount"])
    out, skipped, failures = [], 0, []
    for section, h in enumerate(headers):
        end = headers[section + 1] if section + 1 < len(headers) else len(rows)
        header = [norm(c) for c in rows[h]]
        lower = [header_key(c) for c in header]
        idx = {f: lower.index(header_key(cols[f])) for f in FIELDS
               if cols.get(f) and header_key(cols[f]) in lower}
        missing = [f for f in FIELDS if cols.get(f) and f not in idx]
        if missing:
            print(f"note: columns not found in sheet, ignoring: "
                  f"{', '.join(cols[f] for f in missing)}")
        extra_idx = [i for i, c in enumerate(header) if c and i not in idx.values()]
        last_date = None

        for row in rows[h + 1:end]:
            r = list(row) + [None] * max(0, len(header) - len(row))
            get = lambda f: norm(r[idx[f]]) if f in idx else ""

            def fail(reason: str) -> None:
                failures.append({"source": getattr(row, "origin", ""), "reason": reason,
                                 **{f: get(f) for f in ("date", "amount", "category", "description")},
                                 "row": " | ".join(norm(c) for c in r)})

            # Only the mapped columns count: side panels beside the expense list
            # (e.g. "Total | 112916") share rows with real expenses.
            mapped = [get(f) for f in idx]
            if not any(mapped) or any(TOTAL_RE.match(c) for c in mapped):
                skipped += 1
                continue
            amt = parse_amount(r[idx["amount"]], symbol, decimal, code)
            if amt == 0 or (amt is None and not get("amount")
                            and not (get("description") or get("category"))):
                skipped += 1
                continue
            if amt is None:
                fail("missing amount" if not get("amount") else "unparseable amount")
                skipped += 1
                continue
            d = parse_date(r[idx["date"]], formats)
            if d is None:
                if last_date and not get("date"):
                    d = last_date
                else:
                    fail("missing date" if not get("date") else "unparseable date")
                    skipped += 1
                    continue
            last_date = d
            extra = {header[i]: norm(r[i]) for i in extra_idx if norm(r[i])}
            out.append({
                "date": d.isoformat(),
                "month": d.strftime("%Y-%m"),
                "year": d.year,
                "amount": round(amt, 2),
                "category": get("category").title() or "Uncategorized",
                "subcategory": get("subcategory") or None,
                "description": get("description") or None,
                "payment": get("payment") or None,
                "extra": json.dumps(extra, ensure_ascii=False) if extra else None,
            })

    assign_row_keys(out)
    return out, skipped, failures


def assign_row_keys(records: list[dict]) -> None:
    """Identity = content hash + "nth identical row", so two genuine ₹50 coffees
    on the same day are kept as two rows but re-syncing never duplicates."""
    seen = Counter()
    for rec in records:
        base = "|".join(str(rec[k] or "").lower() for k in
                        ("date", "amount", "category", "subcategory", "description", "payment"))
        seen[base] += 1
        rec["row_key"] = hashlib.sha1(f"{base}#{seen[base]}".encode()).hexdigest()
