"""SQLite schema, migrations, and connections."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from expense_tracker import ExpenseTrackerError

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


def connect(db_path: Path, *, create: bool = False) -> sqlite3.Connection:
    """Open the database with durable settings and an up-to-date schema.

    Only sync creates a new database; everything else needs one to exist.
    """
    if create:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    elif not db_path.exists():
        raise ExpenseTrackerError(f"No database at {db_path}. Run sync.py first.")
    con = sqlite3.connect(db_path, timeout=5)
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 5000")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA synchronous = FULL")
    initialize_database(con)
    return con


def timestamp() -> str:
    return datetime.now().isoformat(timespec="seconds")
