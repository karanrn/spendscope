"""Monthly budgets per Label (expenses.category).

A budget with month '' applies to every month; one with month 'YYYY-MM' overrides it for
that month only.
"""
from __future__ import annotations

import math
import re
import sqlite3

from spendscope import SpendScopeError
from spendscope.db import timestamp
from spendscope.labels import MISSING_LABELS, clean_label

MAX_AMOUNT = 1e12
MONTH_RE = re.compile(r"\d{4}-(0[1-9]|1[0-2])")


def clean_amount(amount: object) -> float:
    """A positive, finite budget amount (numbers or numeric strings)."""
    if isinstance(amount, bool):
        raise SpendScopeError("Budget must be a number")
    try:
        value = float(str(amount).replace(",", "").strip()) if isinstance(amount, str) else float(amount)
    except (TypeError, ValueError):
        raise SpendScopeError("Budget must be a number") from None
    if not math.isfinite(value) or value <= 0 or value >= MAX_AMOUNT:
        raise SpendScopeError("Budget must be a positive amount")
    return value


def clean_month(month: str | None) -> str:
    """'' (every month) or a valid YYYY-MM."""
    month = (month or "").strip()
    if month and not MONTH_RE.fullmatch(month):
        raise SpendScopeError("Month must look like YYYY-MM")
    return month


def budget_label(con: sqlite3.Connection, label: str) -> str:
    """Validate a Label for budgeting, keeping the spelling of an existing Label or budget."""
    label = clean_label(label)
    if label in MISSING_LABELS or label.lower() == "unlabeled":
        raise SpendScopeError("Expenses without a Label cannot have a budget")
    for sql in ("SELECT category FROM expenses WHERE category = ? COLLATE NOCASE LIMIT 1",
                "SELECT label FROM budgets WHERE label = ? LIMIT 1"):
        existing = con.execute(sql, (label,)).fetchone()
        if existing:
            return existing[0]
    return label


def set_budget(con: sqlite3.Connection, label: str, amount: object, month: str | None = "") -> str:
    """Create or replace a budget; returns the Label it was stored under."""
    label, value, month = budget_label(con, label), clean_amount(amount), clean_month(month)
    with con:
        con.execute(
            "INSERT INTO budgets(label, month, amount, updated_at) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(label, month) DO UPDATE SET amount = excluded.amount,"
            " updated_at = excluded.updated_at",
            (label, month, value, timestamp()))
    return label


def remove_budget(con: sqlite3.Connection, label: str, month: str | None = "") -> int:
    """Delete one budget entry; returns 1 if it existed, else 0."""
    with con:
        cur = con.execute("DELETE FROM budgets WHERE label = ? AND month = ?",
                          (clean_label(label), clean_month(month)))
    return cur.rowcount


def all_budgets(con: sqlite3.Connection) -> list[tuple[str, str, float]]:
    """Every budget entry as (label, month, amount); month '' means every month."""
    return con.execute(
        "SELECT label, month, amount FROM budgets ORDER BY label COLLATE NOCASE, month").fetchall()


def month_status(con: sqlite3.Connection, month: str) -> list[tuple[str, float, float, bool]]:
    """(label, budget, spent, month-only?) for each Label with a budget in effect that month."""
    month = clean_month(month)
    if not month:
        raise SpendScopeError("A month (YYYY-MM) is required")
    effective: dict[str, tuple[str, float, bool]] = {}
    for label, entry_month, amount in all_budgets(con):
        if entry_month == month or (entry_month == "" and label.lower() not in effective):
            effective[label.lower()] = (label, amount, entry_month == month)
    spent: dict[str, float] = {}
    for category, total in con.execute(
            "SELECT category, SUM(amount) FROM expenses WHERE month = ? GROUP BY category", (month,)):
        spent[category.lower()] = spent.get(category.lower(), 0) + total
    return [(label, amount, spent.get(key, 0.0), only)
            for key, (label, amount, only) in effective.items()]
