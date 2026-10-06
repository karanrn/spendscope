"""Read raw rows from Google Sheets (API, public CSV, public XLSX) or local files."""
from __future__ import annotations

import csv
import io
import re
import urllib.request
from datetime import date
from pathlib import Path

from expense_tracker import ExpenseTrackerError
from expense_tracker.parsing import header_key, norm


class SheetRow(list):
    """A spreadsheet row that remembers where it came from, for the failures log."""

    def __init__(self, values=(), origin: str = ""):
        super().__init__(values)
        self.origin = origin


def read_rows(src: dict, columns: dict | None = None) -> tuple[list[list], str]:
    """Return every row from the configured source, plus a description of the source."""
    kind = src.get("type", "gsheet_csv")
    if kind == "gsheet_csv":
        return read_public_csv(src)
    if kind == "gsheet_api":
        if columns and columns.get("date"):
            src = {**src, "date_column": columns["date"]}
        return read_google_api(src)
    if kind == "file":
        return read_file(src)
    raise ExpenseTrackerError(f"Unknown source type: {kind}")


def skipped_sheet(title: str, src: dict) -> bool:
    """Summary tabs hold derived totals, not expenses."""
    skip = src.get("skip_worksheets", ["Summary"])
    return norm(title).lower() in {norm(s).lower() for s in skip}


# ----------------------------------------------------------------- local files
def read_file(src: dict) -> tuple[list[list], str]:
    p = Path(src["file_path"])
    if p.suffix.lower() in (".xlsx", ".xlsm"):
        return workbook_rows(p.read_bytes(), f"file:{p.name}", src.get("file_sheet", ""), src)
    with open(p, newline="", encoding="utf-8-sig") as f:
        return [SheetRow(r, f"{p.name}:{i}") for i, r in enumerate(csv.reader(f), 1)], f"file:{p.name}"


def workbook_rows(content: bytes, name: str, file_sheet: str = "",
                  src: dict | None = None) -> tuple[list[list], str]:
    """Extract rows from one or all worksheets in an XLSX workbook."""
    try:
        import openpyxl
    except ImportError:
        raise ExpenseTrackerError("pip install openpyxl   (needed to read .xlsx workbooks)") from None
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    worksheets = ([wb[file_sheet]] if file_sheet else
                  [ws for ws in wb.worksheets if not skipped_sheet(ws.title, src or {})])
    rows, names = [], []
    for ws in worksheets:
        names.append(ws.title)
        rows.extend(SheetRow(r, f"{name}/{ws.title}:{i}")
                    for i, r in enumerate(ws.iter_rows(values_only=True), 1))
        rows.append([])  # do not carry a blank date into the next month's sheet
    wb.close()
    return rows, f"{name}/" + ",".join(names)


# ----------------------------------------------------------------- public Google Sheet
def gsheet_csv_url(url: str) -> str:
    """Turn any Google Sheets URL into its CSV-export URL (keeps the tab gid)."""
    if "format=csv" in url or "output=csv" in url:
        return url
    m = re.search(r"/spreadsheets/d/(e/)?([a-zA-Z0-9_-]+)", url)
    if not m:
        raise ExpenseTrackerError(f"Can't find a sheet ID in: {url}")
    gid = re.search(r"[#&?]gid=(\d+)", url)
    gid_part = f"&gid={gid.group(1)}" if gid else ""
    if m.group(1):  # "Publish to web" link: /d/e/<id>/pub...
        return f"https://docs.google.com/spreadsheets/d/e/{m.group(2)}/pub?output=csv{gid_part}"
    return f"https://docs.google.com/spreadsheets/d/{m.group(2)}/export?format=csv{gid_part}"


def read_public_csv(src: dict) -> tuple[list[list], str]:
    url = gsheet_csv_url(src["sheet_url"])
    req = urllib.request.Request(url, headers={"User-Agent": "expense-sync"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            text = r.read().decode("utf-8-sig")
    except Exception as e:
        raise ExpenseTrackerError(
            f"Couldn't download the sheet ({e}).\n"
            "Check it's shared as 'Anyone with the link can view', "
            "or switch to type = \"gsheet_api\" in config.toml.") from e
    if text.lstrip().lower().startswith("<!doctype html") or "<html" in text[:500].lower():
        raise ExpenseTrackerError(
            "Google returned a sign-in page, so the sheet isn't public. "
            "Share it as 'Anyone with the link can view', or use gsheet_api.")
    return ([SheetRow(r, f"gsheet:{i}") for i, r in enumerate(csv.reader(io.StringIO(text)), 1)],
            "gsheet:" + url)


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
                         src.get("worksheet", ""), src)


# ----------------------------------------------------------------- Google Sheets API
def read_google_api(src: dict) -> tuple[list[list], str]:
    """Read every yearly spreadsheet the authorized account can see."""
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
            raise ExpenseTrackerError(
                "Could not discover yearly Google spreadsheets. Automatic title discovery requires "
                "the Google Drive API to be enabled for the OAuth app's Google Cloud project. "
                f"API error: {api_error}. Enable the Google Drive API and retry, or set source.sheet_id "
                "to import one workbook directly (and source.sheet_url for public XLSX fallback)."
            ) from api_error
        spreadsheets = [{"id": sid, "name": src.get("spreadsheet_title") or sid}]
        print(f"note: Could not discover yearly spreadsheets ({api_error}); trying configured workbook")

    try:
        loaded = [read_api_workbook(gc, spec, src) for spec in spreadsheets]
    except Exception as exc:
        raise ExpenseTrackerError(str(exc)) from exc
    rows = []
    sources = []
    for workbook, source in loaded:
        rows.extend(workbook)
        rows.append([])  # keep blank-date continuation within each workbook only
        sources.append(source)
    return rows, "; ".join(sources)


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
        worksheets = ([spreadsheet.worksheet(src["worksheet"])] if src.get("worksheet") else
                      [ws for ws in spreadsheet.worksheets() if not skipped_sheet(ws.title, src)])
        rows = []
        names = []
        for ws in worksheets:
            names.append(ws.title)
            sheet_rows = [SheetRow(r, f"{spec['name']}/{ws.title}:{i}")
                          for i, r in enumerate(ws.get_all_values(), 1)]
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
