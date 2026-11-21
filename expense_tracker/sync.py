"""Import parsed expenses into SQLite, incrementally or as a mirror of the source.

Expenses come from the configured sheet source (origin 'sheet') or from bank statements
(origin 'bank'). The same spend is often in both, so a bank withdrawal with the same date
and amount (to the rupee; the sheet often rounds) as a sheet row counts as that row: the
sheet's entry wins and the bank's is not stored (or is removed if it was imported first).

After each import, [rules] in config.toml give a Label to expenses that have none.
Statement deposits are stored in the income table when [statements] import_deposits is on.
"""
from __future__ import annotations

import csv
import math
import sqlite3
from collections import Counter
from pathlib import Path

from expense_tracker import db, labels
from expense_tracker.config import database_path, rules
from expense_tracker.parsing import to_records
from expense_tracker.sources import read_rows
from expense_tracker.statements import read_statements

FAILURE_COLUMNS = ["source", "reason", "date", "amount", "category", "description", "row"]


def write_failures(path: Path, failures: list[dict]) -> None:
    """Overwrite the failures log so it always reflects the latest sync."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FAILURE_COLUMNS)
        writer.writeheader()
        writer.writerows(failures)


def insert_expenses(con: sqlite3.Connection, records: list[dict], now: str, sync_id: int) -> None:
    con.executemany(
        "INSERT OR IGNORE INTO expenses(row_key, date, month, year, amount, category, subcategory,"
        " description, payment, extra, origin, first_seen, sync_id) VALUES "
        "(:row_key, :date, :month, :year, :amount, :category, :subcategory, :description,"
        " :payment, :extra, :origin, :first_seen, :sync_id)",
        [{"origin": "sheet", **r, "first_seen": now, "sync_id": sync_id} for r in records])


def apply_rules(con: sqlite3.Connection, cfg: dict, dry_run: bool = False,
                pending: list[dict] | None = None) -> None:
    """Run [rules] over expenses without a Label and report what they set. A dry run counts
    the matches among the stored rows plus the not-yet-stored `pending` records."""
    configured = rules(cfg)
    if not configured:
        return
    counts = labels.apply_rules(con, configured, dry_run=True) if dry_run else labels.apply_rules(con, configured)
    for r in pending or []:
        label = labels.rule_label(r.get("description"), configured) if r.get("category") in labels.MISSING_LABELS else None
        if label:
            counts[label] = counts.get(label, 0) + 1
    total = sum(counts.values())
    if total:
        detail = ", ".join(f"{label} {n}" for label, n in counts.items() if n)
        print(f"{'Rules would set' if dry_run else 'Rules set'} a Label on {total} expenses ({detail})")


def same_spend_key(day: str, amount: float) -> tuple[str, int]:
    """How sheet and bank rows are matched: date and amount rounded half-up to whole units."""
    return day, math.floor(amount + 0.5)


def matching_bank_rows(con: sqlite3.Connection, records: list[dict]) -> list[tuple[int, str]]:
    """(bank expense id, sheet row_key) pairs: one stored bank row per new sheet record
    with the same date and amount, oldest first."""
    pairs, used = [], set()
    for r in records:
        want = same_spend_key(r["date"], r["amount"])
        for bank_id, day, amount in con.execute(
                "SELECT id, date, amount FROM expenses WHERE origin = 'bank' AND date = ? ORDER BY id",
                (r["date"],)):
            if bank_id not in used and same_spend_key(day, amount) == want:
                used.add(bank_id)
                pairs.append((bank_id, r["row_key"]))
                break
    return pairs


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
        latest = con.execute("SELECT MAX(date) FROM expenses WHERE origin = 'sheet'").fetchone()[0]
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
                "SELECT row_key FROM expenses WHERE origin = 'sheet' AND date BETWEEN ? AND ?", (lo, hi))
                if k not in keys]
        replaced = matching_bank_rows(con, new)

        print(f"Read {len(records)} expense rows from {source} "
              f"({skipped} non-expense rows skipped)")
        print(f"  new: {len(new)}   already stored: {len(records) - len(new)}"
              + (f"   removed/edited in sheet: {len(stale)}" if mirror else "")
              + (f"   replacing bank-statement rows: {len(replaced)}" if replaced else ""))
        if dry_run:
            for r in new[:10]:
                print(f"  + {r['date']}  {r['amount']:>10,.2f}  {r['category']:<16} {r['description'] or ''}")
            if len(new) > 10:
                print(f"  ... and {len(new) - 10} more")
            apply_rules(con, cfg, dry_run=True, pending=new)
            print("Dry run: nothing written.")
            return

        now = db.timestamp()
        with con:
            cur = con.execute(
                "INSERT INTO sync_runs(run_at, source, rows_read, inserted, removed, skipped) "
                "VALUES (?,?,?,?,?,?)",
                (now, source, len(records), len(new), len(stale) + len(replaced), skipped))
            insert_expenses(con, new, now, cur.lastrowid)
            if stale:
                con.executemany("DELETE FROM expenses WHERE row_key = ?", [(k,) for k in stale])
            # the sheet row takes over a matching bank row, keeping its additional labels
            for bank_id, row_key in replaced:
                con.execute(
                    "INSERT OR IGNORE INTO expense_labels(expense_id, label, created_at)"
                    " SELECT (SELECT id FROM expenses WHERE row_key = ?), label, created_at"
                    " FROM expense_labels WHERE expense_id = ?", (row_key, bank_id))
                con.execute("DELETE FROM expenses WHERE id = ?", (bank_id,))
        apply_rules(con, cfg)

        total, n = con.execute("SELECT COALESCE(SUM(amount),0), COUNT(*) FROM expenses").fetchone()
        print(f"Database {db_path}: {n} expenses, total {total:,.2f}")
    finally:
        con.close()


def sync_statements(cfg: dict, paths: list[Path], dry_run: bool = False) -> None:
    """Import withdrawals from bank statements, skipping ones already stored or in the sheet."""
    records, deposits = read_statements(paths, cfg)
    import_deposits = bool(cfg.get("statements", {}).get("import_deposits", False))
    source = "statements: " + ", ".join(p.name for p in paths)
    db_path = database_path(cfg)

    con = db.connect(db_path, create=True)
    try:
        sheet = Counter()
        if records:
            sheet.update(same_spend_key(d, a) for d, a in con.execute(
                "SELECT date, amount FROM expenses WHERE origin = 'sheet' AND date BETWEEN ? AND ?",
                (records[0]["date"], records[-1]["date"])))
        # each sheet row covers one bank withdrawal with its date and amount, in statement order
        keep, covered = [], []
        for r in records:
            k = same_spend_key(r["date"], r["amount"])
            if sheet[k]:
                sheet[k] -= 1
                covered.append(r)
            else:
                keep.append(r)
        existing = {k for (k,) in con.execute("SELECT row_key FROM expenses")}
        new = [r for r in keep if r["row_key"] not in existing]
        redundant = [r["row_key"] for r in covered if r["row_key"] in existing]
        stored_income = {k for (k,) in con.execute("SELECT row_key FROM income")}
        new_income = [r for r in deposits if r["row_key"] not in stored_income] if import_deposits else []

        if import_deposits:
            print(f"Read {len(records)} withdrawals and {len(deposits)} deposits from {source}")
        else:
            print(f"Read {len(records)} withdrawals from {source} ({len(deposits)} deposits skipped;"
                  " set import_deposits = true under [statements] to keep them as income)")
        print(f"  new: {len(new)}   already stored: {len(keep) - len(new)}"
              f"   already in sheet: {len(covered)}"
              + (f"   removed (now in sheet): {len(redundant)}" if redundant else "")
              + (f"   new income: {len(new_income)}" if import_deposits else ""))
        if dry_run:
            for r in new[:10]:
                print(f"  + {r['date']}  {r['amount']:>10,.2f}  {r['payment']:<5} {r['description'] or ''}")
            if len(new) > 10:
                print(f"  ... and {len(new) - 10} more")
            apply_rules(con, cfg, dry_run=True, pending=new)
            print("Dry run: nothing written.")
            return

        now = db.timestamp()
        with con:
            cur = con.execute(
                "INSERT INTO sync_runs(run_at, source, rows_read, inserted, removed, skipped) "
                "VALUES (?,?,?,?,?,?)",
                (now, source, len(records), len(new), len(redundant), 0 if import_deposits else len(deposits)))
            insert_expenses(con, new, now, cur.lastrowid)
            con.executemany("DELETE FROM expenses WHERE row_key = ? AND origin = 'bank'",
                            [(k,) for k in redundant])
            con.executemany(
                "INSERT OR IGNORE INTO income(row_key, date, month, year, amount, description, payment,"
                " extra, first_seen, sync_id) VALUES (:row_key, :date, :month, :year, :amount,"
                " :description, :payment, :extra, :first_seen, :sync_id)",
                [{**r, "first_seen": now, "sync_id": cur.lastrowid} for r in new_income])
        apply_rules(con, cfg)

        unlabeled = con.execute("SELECT COUNT(*) FROM expenses WHERE category = ''").fetchone()[0]
        total, n = con.execute("SELECT COALESCE(SUM(amount),0), COUNT(*) FROM expenses").fetchone()
        print(f"Database {db_path}: {n} expenses, total {total:,.2f}; {unlabeled} without a Label")
    finally:
        con.close()
