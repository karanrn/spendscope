#!/usr/bin/env python3
"""Manage additional labels stored alongside the expense database.

Examples:
    python labels.py add Work --source-label Food --item-contains coffee
    python labels.py add Reimbursable --source-label Travel --from 2025-01-01
    python labels.py list
    python labels.py remove Work --source-label Food --item-contains coffee
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import tomllib
from datetime import datetime
from pathlib import Path

from sync import initialize_database


def database_path(config: str) -> Path:
    config_path = Path(config).resolve()
    if not config_path.exists():
        sys.exit(f"Config not found: {config_path}")
    cfg = tomllib.loads(config_path.read_text(encoding="utf-8"))
    db = Path(cfg.get("database", {}).get("path", "expenses.db"))
    return db if db.is_absolute() else config_path.parent / db


def connect(db: Path) -> sqlite3.Connection:
    if not db.exists():
        sys.exit(f"No database at {db}. Run sync.py first.")
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 5000")
    initialize_database(con)
    return con


def matching_expenses(con: sqlite3.Connection, args: argparse.Namespace) -> list[int]:
    clauses = ["category = ? COLLATE NOCASE"]
    params: list[str] = [args.source_label]
    if args.item_contains:
        clauses.append("instr(lower(COALESCE(description, '')), lower(?)) > 0")
        params.append(args.item_contains)
    if args.from_date:
        clauses.append("date >= ?")
        params.append(args.from_date)
    if args.to_date:
        clauses.append("date <= ?")
        params.append(args.to_date)
    return [row[0] for row in con.execute(
        "SELECT id FROM expenses WHERE " + " AND ".join(clauses), params)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.toml")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="show extra labels and how many expenses use them")
    for name in ("add", "remove"):
        command = commands.add_parser(name)
        command.add_argument("label", help="additional label to add or remove")
        command.add_argument("--source-label", required=True,
                             help="existing Label/category to match")
        command.add_argument("--item-contains", help="case-insensitive text contained in Item")
        command.add_argument("--from", dest="from_date", help="inclusive ISO date (YYYY-MM-DD)")
        command.add_argument("--to", dest="to_date", help="inclusive ISO date (YYYY-MM-DD)")
    args = parser.parse_args()
    db = database_path(args.config)
    con = connect(db)

    if args.command == "list":
        labels = con.execute(
            "SELECT label, COUNT(*) FROM expense_labels GROUP BY label ORDER BY label COLLATE NOCASE"
        ).fetchall()
        if labels:
            for label, count in labels:
                print(f"{label}: {count} expenses")
        else:
            print("No additional labels yet.")
    else:
        label = args.label.strip()
        if not label:
            sys.exit("Label cannot be empty.")
        ids = matching_expenses(con, args)
        now = datetime.now().isoformat(timespec="seconds")
        with con:
            if args.command == "add":
                con.executemany(
                    "INSERT OR IGNORE INTO expense_labels(expense_id, label, created_at) VALUES (?, ?, ?)",
                    [(expense_id, label, now) for expense_id in ids])
            else:
                con.executemany(
                    "DELETE FROM expense_labels WHERE expense_id = ? AND label = ? COLLATE NOCASE",
                    [(expense_id, label) for expense_id in ids])
        action = "Added" if args.command == "add" else "Removed"
        print(f"{action} {label!r} on {len(ids)} matching expenses.")
    con.close()


if __name__ == "__main__":
    main()
