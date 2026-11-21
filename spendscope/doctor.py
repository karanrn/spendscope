"""`doctor`: check the config, credentials, source, and database, and say what to fix."""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

from spendscope import SpendScopeError

OK, WARN, FAIL = "ok  ", "warn", "FAIL"


class Report:
    def __init__(self) -> None:
        self.failed = False

    def line(self, status: str, message: str, hint: str = "") -> None:
        self.failed |= status == FAIL
        print(f"[{status}] {message}")
        for part in hint.splitlines() if hint else []:
            print(f"        {part}")


def run_doctor(config: str | None = None, offline: bool = False) -> bool:
    """Print one line per check; returns True when nothing failed."""
    from spendscope.config import database_path, find_config, load_config, rules
    from spendscope.parsing import to_records
    from spendscope.sources import read_rows
    from spendscope.statements import statement_options

    report = Report()
    try:
        path = find_config(config)
        cfg = load_config(path)
        report.line(OK, f"Config: {path}")
    except SpendScopeError as exc:
        report.line(FAIL, "Config", str(exc))
        return False

    for module, why in (("openpyxl", "reading .xlsx workbooks"), ("xlrd", "reading .xls bank statements"),
                        ("gspread", "the Google Sheets API source")):
        if importlib.util.find_spec(module) is None:
            report.line(WARN if module != "openpyxl" else FAIL, f"Python package {module} is missing ({why})",
                        "pip install -r requirements.txt   (or: pip install spendscope)")

    src = cfg["source"]
    kind = src.get("type", "gsheet_csv")
    can_read = check_source(report, src, kind, offline)

    if can_read:
        try:
            rows, source = read_rows(src, cfg.get("columns"))
            records, skipped, failures = to_records(rows, cfg)
            dates = sorted(r["date"] for r in records)
            span = f", {dates[0]} to {dates[-1]}" if dates else ""
            report.line(OK if records else WARN, f"Read {len(records)} expenses from {source}{span}",
                        "" if records else "No expense rows yet: add some, or check [columns] matches your headers.")
            if failures:
                report.line(WARN, f"{len(failures)} rows look like expenses but could not be parsed",
                            "A sync writes them to parse_failures.csv with the reason; check date_formats "
                            "and decimal_separator under [parsing].")
        except SpendScopeError as exc:
            report.line(FAIL, "Reading the source", str(exc))
        except Exception as exc:  # network and Google errors: report, don't crash
            report.line(FAIL, "Reading the source", f"{type(exc).__name__}: {exc}")

    try:
        statement_options(cfg)
        configured = rules(cfg)
        report.line(OK, "[statements] and [rules] settings" + (f" ({len(configured)} rules)" if configured else ""))
    except SpendScopeError as exc:
        report.line(FAIL, "[statements] or [rules] settings", str(exc))

    check_database(report, database_path(cfg))
    print("\nAll checks passed." if not report.failed else "\nFix the FAIL lines above, then run doctor again.")
    return not report.failed


def check_source(report: Report, src: dict, kind: str, offline: bool) -> bool:
    """Check the source's settings and credentials; returns whether reading it is worth trying."""
    from spendscope.sources import source_files, spreadsheet_id

    if kind == "file":
        try:
            files = source_files(src)
        except SpendScopeError as exc:
            report.line(FAIL, "Source file", str(exc))
            return False
        report.line(OK, "Source files: " + ", ".join(p.name for p in files))
        return True
    if kind == "gsheet_csv":
        url = src.get("sheet_url", "")
        if not url or "YOUR_SHEET_ID" in url:
            report.line(FAIL, "source.sheet_url is not set",
                        "Paste your sheet's link (shared as 'Anyone with the link can view').")
            return False
        report.line(OK, f"Public sheet: {url}")
        return not offline
    if kind == "gsheet_api":
        client = Path(src["oauth_client_json"])
        if not client.exists():
            report.line(FAIL, f"OAuth client file not found: {client}",
                        "Create an OAuth client ID of type 'Desktop app' in Google Cloud, with the Google\n"
                        "Sheets API and Google Drive API enabled, and save its JSON there.\n"
                        "See 'Connecting Google Sheets' in the README.")
            return False
        report.line(OK, f"OAuth client: {client.name}")
        token = Path(src["oauth_token_json"])
        if not token.exists():
            report.line(WARN, "Not signed in to Google yet",
                        "Run sync once: it opens a browser to authorize read-only access.\n"
                        "Doctor skips reading the sheets until then, so it never opens a browser itself.")
            return False
        report.line(OK, f"Google token cached: {token.name}",
                    "If sync starts failing every 7 days, the OAuth consent screen is in Testing mode;\n"
                    "publish the app (no verification needed for personal use) to keep tokens valid.")
        if not spreadsheet_id(src) and not src.get("spreadsheet_title_pattern"):
            report.line(WARN, "No spreadsheet_title_pattern or sheet_id: nothing to look for")
        return not offline
    report.line(FAIL, f"Unknown source type {kind!r}", 'Use "file", "gsheet_csv" or "gsheet_api".')
    return False


def check_database(report: Report, db_path: Path) -> None:
    from spendscope.labels import MISSING_SQL

    if not db_path.exists():
        report.line(WARN, f"No database yet at {db_path}", "Run sync to create it.")
        return
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            n, total = con.execute("SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM expenses").fetchone()
            unlabeled = con.execute(f"SELECT COUNT(*) FROM expenses WHERE {MISSING_SQL}").fetchone()[0]
            last = con.execute("SELECT run_at FROM sync_runs ORDER BY id DESC LIMIT 1").fetchone()
        finally:
            con.close()
    except sqlite3.Error as exc:
        report.line(FAIL, f"Database {db_path}", str(exc))
        return
    report.line(OK, f"Database: {n} expenses, total {total:,.2f}, last sync {last[0] if last else 'never'}")
    if unlabeled:
        report.line(WARN, f"{unlabeled} expenses have no Label",
                    "Set them in the dashboard, with `labels set-primary`, or with [rules] in config.toml.")
