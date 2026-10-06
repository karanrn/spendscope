"""Import parsed expenses into SQLite, incrementally or as a mirror of the source."""
from __future__ import annotations

import csv
from pathlib import Path

from expense_tracker import db
from expense_tracker.config import database_path
from expense_tracker.parsing import to_records
from expense_tracker.sources import read_rows

FAILURE_COLUMNS = ["source", "reason", "date", "amount", "category", "description", "row"]


def write_failures(path: Path, failures: list[dict]) -> None:
    """Overwrite the failures log so it always reflects the latest sync."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FAILURE_COLUMNS)
        writer.writeheader()
        writer.writerows(failures)


def sync(cfg: dict, mirror: bool = False, dry_run: bool = False, full_rescan: bool = False) -> None:
    rows, source = read_rows(cfg["source"], cfg.get("columns"))
    records, skipped, failures = to_records(rows, cfg)
    failures_path = Path(cfg["parsing"]["failures_log"])
    write_failures(failures_path, failures)
    if failures:
        print(f"warning: {len(failures)} rows could not be parsed; see {failures_path}")
    db_path = database_path(cfg)

    con = db.connect(db_path, create=True)
    try:
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
            return

        now = db.timestamp()
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
    finally:
        con.close()
