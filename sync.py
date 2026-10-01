#!/usr/bin/env python3
"""
Sync expenses from a Google Sheet (or a local .xlsx/.csv) into SQLite.

Safe to run as often as you like: rows already in the database are skipped,
so history builds up over time without duplicates.

    python sync.py                 # sync using config.toml
    python sync.py --full-rescan   # scan all history (default starts at latest DB date)
    python sync.py --mirror        # also apply edits/deletions made in the source
    python sync.py --dry-run       # show what would change, write nothing
    python sync.py --config other.toml

Install dependencies with: pip install -r requirements.txt
Google Sheets access uses an OAuth installed-app client; openpyxl reads
downloaded XLSX fallbacks and local .xlsx files.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sqlite3
import sys
import tomllib
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

TOTAL_RE = re.compile(r"(?i)^(grand\s*|sub\s*)?totals?:?$")
FIELDS = ["date", "amount", "category", "description", "payment", "subcategory"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS expenses (
    id          INTEGER PRIMARY KEY,
    row_key     TEXT NOT NULL UNIQUE,   -- content hash + occurrence no., used for dedup
    date        TEXT NOT NULL,          -- ISO yyyy-mm-dd
    month       TEXT NOT NULL,          -- yyyy-mm, handy for grouping
    year        INTEGER NOT NULL,       -- calendar year, handy for yearly grouping
    amount      REAL NOT NULL,
    category    TEXT NOT NULL DEFAULT 'Uncategorized',
    subcategory TEXT,
    description TEXT,
    payment     TEXT,
    extra       TEXT,                   -- JSON of any unmapped columns
    first_seen  TEXT NOT NULL,          -- when this row first came in
    sync_id     INTEGER REFERENCES sync_runs(id)
);
CREATE INDEX IF NOT EXISTS idx_exp_date     ON expenses(date);
CREATE INDEX IF NOT EXISTS idx_exp_month    ON expenses(month);
CREATE INDEX IF NOT EXISTS idx_exp_category ON expenses(category);

CREATE TABLE IF NOT EXISTS expense_labels (
    expense_id  INTEGER NOT NULL REFERENCES expenses(id) ON DELETE CASCADE,
    label       TEXT NOT NULL COLLATE NOCASE,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (expense_id, label)
);
CREATE INDEX IF NOT EXISTS idx_expense_labels_label ON expense_labels(label);

CREATE TABLE IF NOT EXISTS sync_runs (
    id          INTEGER PRIMARY KEY,
    run_at      TEXT NOT NULL,
    source      TEXT NOT NULL,
    rows_read   INTEGER NOT NULL,
    inserted    INTEGER NOT NULL,
    removed     INTEGER NOT NULL,
    skipped     INTEGER NOT NULL
);
"""


def initialize_database(con: sqlite3.Connection) -> None:
    """Create the schema and migrate older databases to include expense year."""
    con.executescript(SCHEMA)
    columns = {row[1] for row in con.execute("PRAGMA table_info(expenses)")}
    if "year" not in columns:
        con.execute("ALTER TABLE expenses ADD COLUMN year INTEGER")
    con.execute(
        "UPDATE expenses SET year = CAST(substr(date, 1, 4) AS INTEGER) WHERE year IS NULL"
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_exp_year ON expenses(year)")
    con.commit()


# ----------------------------------------------------------------- reading
def gsheet_csv_url(url: str) -> str:
    """Turn any Google Sheets URL into its CSV-export URL (keeps the tab gid)."""
    if "format=csv" in url or "output=csv" in url:
        return url
    m = re.search(r"/spreadsheets/d/(e/)?([a-zA-Z0-9_-]+)", url)
    if not m:
        sys.exit(f"Can't find a sheet ID in: {url}")
    gid = re.search(r"[#&?]gid=(\d+)", url)
    gid_part = f"&gid={gid.group(1)}" if gid else ""
    if m.group(1):  # "Publish to web" link: /d/e/<id>/pub...
        return f"https://docs.google.com/spreadsheets/d/e/{m.group(2)}/pub?output=csv{gid_part}"
    return f"https://docs.google.com/spreadsheets/d/{m.group(2)}/export?format=csv{gid_part}"


def workbook_rows(content: bytes, name: str, file_sheet: str = "") -> tuple[list[list], str]:
    """Extract rows from one or all worksheets in an XLSX workbook."""
    try:
        import openpyxl
    except ImportError:
        sys.exit("pip install openpyxl   (needed to read .xlsx workbooks)")
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    worksheets = [wb[file_sheet]] if file_sheet else wb.worksheets
    rows, names = [], []
    for ws in worksheets:
        names.append(ws.title)
        rows.extend([list(r) for r in ws.iter_rows(values_only=True)])
        rows.append([])  # do not carry a blank date into the next month's sheet
    wb.close()
    return rows, f"{name}/" + ",".join(names)


def spreadsheet_id(src: dict) -> str | None:
    """Find the spreadsheet ID from an explicit key or its Google URL."""
    if src.get("sheet_id") and src["sheet_id"] != "YOUR_SHEET_ID":
        return src["sheet_id"]
    match = re.search(r"/spreadsheets/d/(?:e/)?([a-zA-Z0-9_-]+)", src.get("sheet_url", ""))
    candidate = match.group(1) if match else None
    return candidate if candidate and candidate != "YOUR_SHEET_ID" else None


def read_google_xlsx_fallback(
    src: dict, sheet_id: str, name: str | None = None
) -> tuple[list[list], str]:
    """Download a public spreadsheet as XLSX if the authenticated API is unavailable."""
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=xlsx"
    req = urllib.request.Request(url, headers={"User-Agent": "expense-sync"})
    with urllib.request.urlopen(req, timeout=60) as response:
        content = response.read()
    return workbook_rows(content, f"google-xlsx:{name or src.get('spreadsheet_title') or sheet_id}",
                         src.get("worksheet", ""))


def yearly_spreadsheets(gc, src: dict) -> list[dict[str, str]]:
    """Find accessible spreadsheets matching the configured yearly title pattern."""
    pattern = re.compile(src.get("spreadsheet_title_pattern", r"^Expenses \d{4}$"))
    files = gc.list_spreadsheet_files(folder_id=src.get("folder_id") or None)
    matches = [f for f in files if pattern.fullmatch(f.get("name", ""))]
    if matches:
        matches.sort(key=lambda f: (re.search(r"\d{4}$", f["name"])[0], f["name"]))
        return [{"id": f["id"], "name": f["name"]} for f in matches]

    # Keep one-workbook configurations working when auto-discovery is disabled
    # or the configured spreadsheet is named differently from the pattern.
    sid = spreadsheet_id(src)
    if sid:
        return [{"id": sid, "name": src.get("spreadsheet_title") or sid}]
    title = src.get("spreadsheet_title", "")
    if title and title != "Expenses YYYY":
        spreadsheet = gc.open(title)
        return [{"id": spreadsheet.id, "name": spreadsheet.title}]
    raise ValueError(f"No accessible spreadsheet matches title pattern {pattern.pattern!r}")


def read_api_workbook(gc, spec: dict[str, str], src: dict) -> tuple[list[list], str]:
    """Read one annual spreadsheet through Sheets API, with its XLSX fallback."""
    try:
        spreadsheet = gc.open_by_key(spec["id"])
        year_match = re.search(r"(\d{4})$", spec["name"])
        workbook_year = int(year_match.group(1)) if year_match else None
        worksheets = ([spreadsheet.worksheet(src["worksheet"])]
                      if src.get("worksheet") else spreadsheet.worksheets())
        rows = []
        names = []
        for ws in worksheets:
            names.append(ws.title)
            sheet_rows = ws.get_all_values()
            if workbook_year:
                add_year_to_short_dates(sheet_rows, workbook_year, src.get("date_column", "Date"))
            rows.extend(sheet_rows)
            rows.append([])  # reset blank-date continuation between monthly sheets
        return rows, f"gsheet_api:{spec['name']}/" + ",".join(names)
    except Exception as api_error:
        try:
            rows, source = read_google_xlsx_fallback(src, spec["id"], spec["name"])
            print(f"note: Sheets API unavailable for {spec['name']} ({api_error}); using public XLSX download")
            return rows, source
        except Exception as fallback_error:
            raise RuntimeError(
                f"Could not read {spec['name']} through the API ({api_error}) or XLSX fallback "
                f"({fallback_error}); confirm API access or public link access."
            ) from fallback_error


def add_year_to_short_dates(rows: list[list], year: int, date_column: str = "Date") -> None:
    """Add the containing annual workbook's year to API dates formatted as M/D."""
    date_idx = None
    for row in rows:
        normalized = [header_key(norm(value)) for value in row]
        if header_key(date_column) in normalized:
            date_idx = normalized.index(header_key(date_column))
            continue
        if date_idx is None or date_idx >= len(row):
            continue
        value = norm(row[date_idx])
        if re.fullmatch(r"\d{1,2}/\d{1,2}", value):
            month, day = (int(part) for part in value.split("/"))
            try:
                row[date_idx] = date(year, month, day).isoformat()
            except ValueError:
                continue


def read_rows(src: dict, columns: dict | None = None) -> tuple[list[list], str]:
    kind = src.get("type", "gsheet_csv")

    if kind == "gsheet_csv":
        url = gsheet_csv_url(src["sheet_url"])
        req = urllib.request.Request(url, headers={"User-Agent": "expense-sync"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                text = r.read().decode("utf-8-sig")
        except Exception as e:
            sys.exit(f"Couldn't download the sheet ({e}).\n"
                     "Check it's shared as 'Anyone with the link can view', "
                     "or switch to type = \"gsheet_api\" in config.toml.")
        if text.lstrip().lower().startswith("<!doctype html") or "<html" in text[:500].lower():
            sys.exit("Google returned a sign-in page, so the sheet isn't public. "
                     "Share it as 'Anyone with the link can view', or use gsheet_api.")
        return list(csv.reader(io.StringIO(text))), "gsheet:" + url

    if kind == "gsheet_api":
        if columns and columns.get("date"):
            src = {**src, "date_column": columns["date"]}
        gc = None
        try:
            import gspread
            Path(src["oauth_token_json"]).parent.mkdir(parents=True, exist_ok=True)
            gc = gspread.oauth(
                credentials_filename=src["oauth_client_json"],
                authorized_user_filename=src["oauth_token_json"],
                scopes=gspread.auth.READONLY_SCOPES,
            )
            spreadsheets = yearly_spreadsheets(gc, src)
        except Exception as api_error:
            sid = spreadsheet_id(src)
            if not sid:
                sys.exit(
                    "Could not discover yearly Google spreadsheets. Automatic title discovery requires "
                    "the Google Drive API to be enabled for the OAuth app's Google Cloud project. "
                    f"API error: {api_error}. Enable the Google Drive API and retry, or set source.sheet_id "
                    "to import one workbook directly (and source.sheet_url for public XLSX fallback)."
                )
            spreadsheets = [{"id": sid, "name": src.get("spreadsheet_title") or sid}]
            print(f"note: Could not discover yearly spreadsheets ({api_error}); trying configured workbook")

        try:
            loaded = [read_api_workbook(gc, spec, src) for spec in spreadsheets]
        except Exception as exc:
            sys.exit(str(exc))
        rows = []
        sources = []
        for workbook, source in loaded:
            rows.extend(workbook)
            rows.append([])  # keep blank-date continuation within each workbook only
            sources.append(source)
        return rows, "; ".join(sources)

    if kind == "file":
        p = Path(src["file_path"])
        if p.suffix.lower() in (".xlsx", ".xlsm"):
            return workbook_rows(p.read_bytes(), f"file:{p.name}", src.get("file_sheet", ""))
        with open(p, newline="", encoding="utf-8-sig") as f:
            return list(csv.reader(f)), f"file:{p.name}"

    sys.exit(f"Unknown source type: {kind}")


# ----------------------------------------------------------------- parsing
def norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def header_key(value: str) -> str:
    """Normalize optional units such as INR in spreadsheet column headings."""
    return re.sub(r"\s*\([^)]*\)", "", norm(value)).strip().lower()


def parse_amount(v, symbol: str) -> float | None:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    s = norm(v)
    if not s:
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = re.sub(r"(?i)\b(rs\.?|inr)\b", "", s)
    s = s.replace(symbol, "").replace(",", "").replace("(", "").replace(")", "").strip()
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
    sys.exit(f"Couldn't find a header row with columns '{date_col}' and '{amount_col}'.\n"
             f"First row seen: {rows[0] if rows else '(empty sheet)'}\n"
             "Fix the names under [columns] in config.toml.")


def to_records(rows: list[list], cfg: dict) -> tuple[list[dict], int]:
    cols, parsing = cfg["columns"], cfg.get("parsing", {})
    formats = parsing.get("date_formats", ["%d/%m/%Y", "%Y-%m-%d"])
    symbol = parsing.get("currency_symbol", "₹")

    headers = find_headers(rows, cols["date"], cols["amount"])
    out, skipped = [], 0
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
            if any(TOTAL_RE.match(norm(c)) for c in r if isinstance(c, str)):
                skipped += 1
                continue
            amt = parse_amount(r[idx["amount"]], symbol)
            if amt is None or amt == 0:
                skipped += 1
                continue
            d = parse_date(r[idx["date"]], formats)
            if d is None:
                if last_date and not norm(r[idx["date"]]):
                    d = last_date
                else:
                    skipped += 1
                    continue
            last_date = d
            get = lambda f: norm(r[idx[f]]) if f in idx else ""
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

    # Identity = content hash + "nth identical row", so two genuine ₹50 coffees
    # on the same day are kept as two rows but re-syncing never duplicates.
    seen = Counter()
    for rec in out:
        base = "|".join(str(rec[k] or "").lower() for k in
                        ("date", "amount", "category", "subcategory", "description", "payment"))
        seen[base] += 1
        rec["row_key"] = hashlib.sha1(f"{base}#{seen[base]}".encode()).hexdigest()
    return out, skipped


# ----------------------------------------------------------------- storing
def sync(cfg: dict, mirror: bool, dry_run: bool, full_rescan: bool = False) -> None:
    rows, source = read_rows(cfg["source"], cfg.get("columns"))
    records, skipped = to_records(rows, cfg)
    db_path = Path(cfg["database"]["path"])
    db_path.parent.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 5000")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA synchronous = FULL")
    initialize_database(con)
    latest = con.execute("SELECT MAX(date) FROM expenses").fetchone()[0]
    if latest and not (mirror or full_rescan):
        before = len(records)
        records = [r for r in records if r["date"] >= latest]
        print(f"Incremental scan starts at {latest}; ignored {before - len(records)} older source rows")
    existing = {k for (k,) in con.execute("SELECT row_key FROM expenses")}
    new = [r for r in records if r["row_key"] not in existing]

    stale: list[str] = []
    if mirror and records:
        lo = min(r["date"] for r in records)
        hi = max(r["date"] for r in records)
        keys = {r["row_key"] for r in records}
        stale = [k for (k,) in con.execute(
            "SELECT row_key FROM expenses WHERE date BETWEEN ? AND ?", (lo, hi))
            if k not in keys]

    print(f"Read {len(records)} expense rows from {source} "
          f"({skipped} non-expense rows skipped)")
    print(f"  new: {len(new)}   already stored: {len(records) - len(new)}"
          + (f"   removed/edited in sheet: {len(stale)}" if mirror else ""))
    if dry_run:
        for r in new[:10]:
            print(f"  + {r['date']}  {r['amount']:>10,.2f}  {r['category']:<16} {r['description'] or ''}")
        if len(new) > 10:
            print(f"  ... and {len(new) - 10} more")
        print("Dry run: nothing written.")
        con.close()
        return

    now = datetime.now().isoformat(timespec="seconds")
    with con:
        cur = con.execute(
            "INSERT INTO sync_runs(run_at, source, rows_read, inserted, removed, skipped) "
            "VALUES (?,?,?,?,?,?)", (now, source, len(records), len(new), len(stale), skipped))
        sync_id = cur.lastrowid
        con.executemany(
            "INSERT OR IGNORE INTO expenses(row_key, date, month, year, amount, category, subcategory,"
            " description, payment, extra, first_seen, sync_id) VALUES "
            "(:row_key, :date, :month, :year, :amount, :category, :subcategory, :description,"
            " :payment, :extra, :first_seen, :sync_id)",
            [{**r, "first_seen": now, "sync_id": sync_id} for r in new])
        if stale:
            con.executemany("DELETE FROM expenses WHERE row_key = ?", [(k,) for k in stale])

    total, n = con.execute("SELECT COALESCE(SUM(amount),0), COUNT(*) FROM expenses").fetchone()
    print(f"Database {db_path}: {n} expenses, total {total:,.2f}")
    con.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--mirror", action="store_true",
                    help="delete DB rows (within the sheet's date range) that are no longer in the sheet, "
                         "so edits and deletions carry over")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--full-rescan", action="store_true",
                    help="scan all source dates instead of starting at the latest date in the database")
    a = ap.parse_args()
    cfg_path = Path(a.config)
    if not cfg_path.exists():
        sys.exit(f"Config not found: {cfg_path}")
    cfg = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    # make relative paths in config relative to the config file
    base = cfg_path.resolve().parent
    for sect, key in (("database", "path"), ("source", "file_path"),
                      ("source", "oauth_client_json"), ("source", "oauth_token_json")):
        if cfg.get(sect, {}).get(key) and not Path(cfg[sect][key]).is_absolute():
            cfg[sect][key] = str(base / cfg[sect][key])
    sync(cfg, a.mirror, a.dry_run, a.full_rescan)


if __name__ == "__main__":
    main()
