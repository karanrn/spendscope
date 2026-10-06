"""Additional labels stored alongside expenses, separate from the workbook's Label."""
from __future__ import annotations

import sqlite3

from expense_tracker import ExpenseTrackerError
from expense_tracker.db import timestamp

MAX_LABEL_LENGTH = 80


def clean_label(label: str) -> str:
    """Strip a label and reject empty, overlong or control-character labels."""
    label = (label or "").strip()
    if not label or len(label) > MAX_LABEL_LENGTH or any(ord(c) < 32 for c in label):
        raise ExpenseTrackerError(f"Label must contain 1–{MAX_LABEL_LENGTH} printable characters")
    return label


def find_expenses(con: sqlite3.Connection, source_label: str, item_contains: str | None = None,
                  from_date: str | None = None, to_date: str | None = None) -> list[int]:
    """IDs of expenses with this workbook Label, optionally narrowed by Item text and dates."""
    clauses = ["category = ? COLLATE NOCASE"]
    params: list[str] = [source_label]
    if item_contains:
        clauses.append("instr(lower(COALESCE(description, '')), lower(?)) > 0")
        params.append(item_contains)
    if from_date:
        clauses.append("date >= ?")
        params.append(from_date)
    if to_date:
        clauses.append("date <= ?")
        params.append(to_date)
    return [row[0] for row in con.execute(
        "SELECT id FROM expenses WHERE " + " AND ".join(clauses), params)]


def add_label(con: sqlite3.Connection, expense_ids: list[int], label: str) -> int:
    """Attach a label; returns how many expenses gained it (already-labelled ones are skipped)."""
    now = timestamp()
    with con:
        cur = con.executemany(
            "INSERT OR IGNORE INTO expense_labels(expense_id, label, created_at) VALUES (?, ?, ?)",
            [(expense_id, label, now) for expense_id in expense_ids])
    return cur.rowcount


def remove_label(con: sqlite3.Connection, expense_ids: list[int], label: str) -> int:
    """Detach a label; returns how many expenses actually had it."""
    with con:
        cur = con.executemany(
            "DELETE FROM expense_labels WHERE expense_id = ? AND label = ? COLLATE NOCASE",
            [(expense_id, label) for expense_id in expense_ids])
    return cur.rowcount


def expense_exists(con: sqlite3.Connection, expense_id: int) -> bool:
    return con.execute("SELECT 1 FROM expenses WHERE id = ?", (expense_id,)).fetchone() is not None


def labels_for(con: sqlite3.Connection, expense_id: int) -> list[str]:
    return [row[0] for row in con.execute(
        "SELECT label FROM expense_labels WHERE expense_id = ? ORDER BY label COLLATE NOCASE",
        (expense_id,))]


def all_labels(con: sqlite3.Connection) -> list[str]:
    return [row[0] for row in con.execute(
        "SELECT DISTINCT label FROM expense_labels ORDER BY label COLLATE NOCASE")]


def label_counts(con: sqlite3.Connection) -> list[tuple[str, int]]:
    return con.execute(
        "SELECT label, COUNT(*) FROM expense_labels GROUP BY label ORDER BY label COLLATE NOCASE"
    ).fetchall()
