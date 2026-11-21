"""SQLite schema, migrations, and connections."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from spendscope import SpendScopeError

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
    origin      TEXT NOT NULL DEFAULT 'sheet',  -- 'sheet' or 'bank' (statement import)
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

CREATE TABLE IF NOT EXISTS budgets (
    label       TEXT NOT NULL COLLATE NOCASE,   -- a Label (expenses.category)
    month       TEXT NOT NULL DEFAULT '',       -- yyyy-mm, or '' for every month
    amount      REAL NOT NULL CHECK (amount > 0),
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (label, month)
);

-- bank-statement deposits, imported only with [statements] import_deposits = true
CREATE TABLE IF NOT EXISTS income (
    id          INTEGER PRIMARY KEY,
    row_key     TEXT NOT NULL UNIQUE,   -- date, ref, amount and closing balance, like bank expenses
    date        TEXT NOT NULL,
    month       TEXT NOT NULL,
    year        INTEGER NOT NULL,
    amount      REAL NOT NULL,
    description TEXT,
    payment     TEXT,
    extra       TEXT,
    first_seen  TEXT NOT NULL,
    sync_id     INTEGER REFERENCES sync_runs(id)
);
CREATE INDEX IF NOT EXISTS idx_income_month ON income(month);

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
    """Create the schema and migrate older databases to include expense year and origin."""
    con.executescript(SCHEMA)
    columns = {row[1] for row in con.execute("PRAGMA table_info(expenses)")}
    if "year" not in columns:
        con.execute("ALTER TABLE expenses ADD COLUMN year INTEGER")
    con.execute(
        "UPDATE expenses SET year = CAST(substr(date, 1, 4) AS INTEGER) WHERE year IS NULL"
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_exp_year ON expenses(year)")
    if "origin" not in columns:
        con.execute("ALTER TABLE expenses ADD COLUMN origin TEXT NOT NULL DEFAULT 'sheet'")
    con.execute("CREATE INDEX IF NOT EXISTS idx_exp_origin_date ON expenses(origin, date)")
    con.commit()


def connect(db_path: Path, *, create: bool = False) -> sqlite3.Connection:
    """Open the database with durable settings and an up-to-date schema.

    Only sync creates a new database; everything else needs one to exist.
    """
    if create:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    elif not db_path.exists():
        raise SpendScopeError(
            f"No database at {db_path}. Import your expenses first: python -m spendscope sync")
    con = sqlite3.connect(db_path, timeout=5)
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 5000")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA synchronous = FULL")
    initialize_database(con)
    return con


def backup(db_path: Path, out: Path | None = None) -> Path:
    """Copy the database safely, even while it is in use; returns the copy's path.

    The default destination is backups/expenses-<timestamp>.db next to the database.
    """
    if not db_path.exists():
        raise SpendScopeError(f"No database at {db_path} to back up.")
    if out is None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out = db_path.parent / "backups" / f"{db_path.stem}-{stamp}{db_path.suffix or '.db'}"
    if out.exists():
        raise SpendScopeError(f"{out} already exists; choose another --out.")
    out.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(out)
    try:
        with dst:
            src.backup(dst)
    finally:
        dst.close()
        src.close()
    return out


def timestamp() -> str:
    return datetime.now().isoformat(timespec="seconds")
