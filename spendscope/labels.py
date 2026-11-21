"""Additional labels stored alongside expenses, separate from the workbook's Label."""
from __future__ import annotations

import sqlite3

from spendscope import SpendScopeError
from spendscope.db import timestamp

MAX_LABEL_LENGTH = 80
# Label (category) values that mean "no Label yet": bank imports store "", and sheet rows
# with an empty Label column get "Uncategorized". Only these can be given a Label here.
MISSING_LABELS = ("", "Uncategorized")
MISSING_SQL = "category IN (%s)" % ", ".join("'%s'" % v for v in MISSING_LABELS)


def clean_label(label: str) -> str:
    """Strip a label and reject empty, overlong or control-character labels."""
    label = (label or "").strip()
    if not label or len(label) > MAX_LABEL_LENGTH or any(ord(c) < 32 for c in label):
        raise SpendScopeError(f"Label must contain 1–{MAX_LABEL_LENGTH} printable characters")
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


def find_unlabeled(con: sqlite3.Connection, item_contains: str | None = None,
                   from_date: str | None = None, to_date: str | None = None) -> list[int]:
    """IDs of expenses without a Label, optionally narrowed by Item text and dates."""
    clauses, params = [MISSING_SQL], []
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


def set_primary_label(con: sqlite3.Connection, expense_ids: list[int], label: str) -> int:
    """Give expenses that have no Label this one; returns how many changed (labelled ones are left alone).

    An existing Label matching case-insensitively keeps its spelling, so "food" joins "Food".
    """
    existing = con.execute(
        "SELECT category FROM expenses WHERE category = ? COLLATE NOCASE LIMIT 1", (label,)).fetchone()
    if existing and existing[0] not in MISSING_LABELS:
        label = existing[0]
    with con:
        cur = con.executemany(f"UPDATE expenses SET category = ? WHERE id = ? AND {MISSING_SQL}",
                              [(label, expense_id) for expense_id in expense_ids])
    return cur.rowcount


def primary_label(con: sqlite3.Connection, expense_id: int) -> str:
    return con.execute("SELECT category FROM expenses WHERE id = ?", (expense_id,)).fetchone()[0]


def unlabeled_count(con: sqlite3.Connection) -> int:
    return con.execute(f"SELECT COUNT(*) FROM expenses WHERE {MISSING_SQL}").fetchone()[0]


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


def rule_label(description: str | None, rules: dict[str, list[str]]) -> str | None:
    """The Label of the first rule with a keyword in this Item (case-insensitive), if any."""
    text = (description or "").lower()
    return next((label for label, words in rules.items()
                 if any(w.lower() in text for w in words)), None)


def apply_rules(con: sqlite3.Connection, rules: dict[str, list[str]],
                dry_run: bool = False) -> dict[str, int]:
    """Give expenses without a Label the Label of the first rule that matches their Item.

    Rules come from config.toml's [rules] (Label → keywords) and are tried in order. Expenses
    that already have a Label are never changed. Returns how many expenses each Label got.
    """
    pending: dict[str, list[int]] = {}
    for expense_id, description in con.execute(
            f"SELECT id, description FROM expenses WHERE {MISSING_SQL} ORDER BY id"):
        label = rule_label(description, rules)
        if label:
            pending.setdefault(label, []).append(expense_id)
    if dry_run:
        return {label: len(ids) for label, ids in pending.items()}
    return {label: set_primary_label(con, ids, clean_label(label)) for label, ids in pending.items()}
