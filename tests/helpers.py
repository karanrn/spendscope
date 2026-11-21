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
        self.root = Path(self._tmp.name).resolve()  # /var → /private/var on macOS, 8.3 names on Windows
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


# A made-up bank statement in the bank's comma-delimited .txt layout: padded fields,
# truncated headers, one deposit. Against CSV_ROWS, the first two withdrawals are
# already in the sheet (449.60 is the sheet's 450 to the rupee); the third is not.
STATEMENT_TXT = [
    "",
    "  Date     ,Narration                        ,Value Dat,Debit Amount       ,Credit Amount      ,Chq/Ref Number   ,Closing Balance",
    " 01/01/26  ,UPI-CAFE COFFEE-PAYMENT FROM PHONE  ,01/01/26 ,         449.60     ,           0.00     ,0000000000000111       ,        9550.40  ",
    " 01/01/26  ,UPI-A FRIEND-SPLIT                  ,01/01/26 ,           0.00     ,         200.00     ,0000000000000112       ,        9750.40  ",
    " 02/01/26  ,POS 400000XXXXXX0001 GROCER         ,02/01/26 ,        1500.00     ,           0.00     ,0000000000000113       ,        8250.40  ",
    " 03/01/26  ,ACH D- GROWW PAY SERVICES P-0000ABC ,03/01/26 ,        5000.00     ,           0.00     ,0000000000000114       ,        3250.40  ",
]

# The same transactions in the bank's spreadsheet layout (its .xls export, saved as .xlsx
# here): a preamble, differently named headers, asterisk rules and a summary block.
STATEMENT_SHEET = [
    ["HDFC BANK Ltd.          Statement of accounts"],
    ["Statement From  :  01/01/2026         To  :  03/01/2026"],
    ["*" * 40],
    ["Date", "Narration", "Chq./Ref.No.", "Value Dt", "Withdrawal Amt.", "Deposit Amt.", "Closing Balance"],
    ["********", "*****", "*****", "********", "*****", "*****", "*****"],
    ["01/01/26", "UPI-CAFE COFFEE-PAYMENT FROM PHONE", "0000000000000111", "01/01/26", 449.6, None, 9550.4],
    ["01/01/26", "UPI-A FRIEND-SPLIT", "0000000000000112", "01/01/26", None, 200.0, 9750.4],
    ["02/01/26", "POS 400000XXXXXX0001 GROCER", "0000000000000113", "02/01/26", 1500.0, None, 8250.4],
    ["03/01/26", "ACH D- GROWW PAY SERVICES P-0000ABC", "0000000000000114", "03/01/26", 5000.0, None, 3250.4],
    [None],
    ["STATEMENT SUMMARY  :-"],
    ["Opening Balance", None, None, None, "Debits", "Credits", "Closing Bal"],
    [10000.0, None, None, None, 6949.6, 200.0, 3250.4],
]


def write_statements(root: Path) -> tuple[Path, Path]:
    """Write STATEMENT_TXT and STATEMENT_SHEET under root; returns (txt path, xlsx path)."""
    from openpyxl import Workbook
    txt = root / "statement.txt"
    txt.write_text("\r\n".join(STATEMENT_TXT) + "\r\n", encoding="utf-8", newline="")  # keep CRLF as is on Windows
    wb = Workbook()
    for row in STATEMENT_SHEET:
        wb.active.append(row)
    xlsx = root / "statement.xlsx"
    wb.save(xlsx)
    return txt, xlsx


def add_config(project: Project, text: str) -> None:
    """Append TOML sections to the project's config."""
    with open(project.config_path, "a", encoding="utf-8") as f:
        f.write("\n" + text + "\n")
