"""Shared fixtures: a temporary project folder with a CSV source and config."""
from __future__ import annotations

import contextlib
import io
import tempfile
from pathlib import Path

from expense_tracker.config import load_config

CONFIG = """
[source]
type = "file"
file_path = "expenses.csv"

[database]
path = "data/expenses.db"

[columns]
date = "Date"
amount = "Expense"
category = "Label"
description = "Item"
payment = ""
subcategory = ""

[parsing]
date_formats = ["%d/%m/%Y", "%Y-%m-%d"]
failures_log = "parse_failures.csv"
"""

CSV_ROWS = [
    "Date,Expense(INR),Item,Label,,Month,January",
    "01/01/2026,450,Coffee and sandwich,Food,,Total,1950",
    "02/01/2026,1500,Groceries,Grocery,,,",
    "02/01/2026,1500,Groceries,Grocery,,,",
    ",0,Free sample,Misc,,,",
]


class Project:
    """A throwaway project folder; write_csv() replaces the source rows."""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "config.toml").write_text(CONFIG, encoding="utf-8")
        self.write_csv(CSV_ROWS)

    def write_csv(self, rows: list[str]) -> None:
        (self.root / "expenses.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")

    @property
    def config_path(self) -> Path:
        return self.root / "config.toml"

    def config(self) -> dict:
        return load_config(self.config_path)

    def cleanup(self) -> None:
        self._tmp.cleanup()


def quietly(fn, *args, **kwargs):
    """Call fn with stdout captured; returns (result, printed text)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = fn(*args, **kwargs)
    return result, out.getvalue()
