"""Write the yearly expenses workbook template for any year.

The bundled template is the 2026 workbook: a Summary tab and month tabs Jan26 … Dec26 whose
names the Summary formulas and chart refer to. Another year's copy renames the tabs inside the
xlsx's XML, so formulas, the chart, the Label dropdown and formatting all carry over (loading
and saving it with openpyxl would drop the chart).
"""
from __future__ import annotations

import re
import zipfile
from datetime import date
from pathlib import Path

from expense_tracker import ExpenseTrackerError

TEMPLATE_XLSX = Path(__file__).resolve().parent / "templates" / "expenses-template.xlsx"
TEMPLATE_YEAR = 2026
MONTH_TAB = re.compile(rf"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec){TEMPLATE_YEAR % 100:02d}\b")


def workbook_name(year: int) -> str:
    """The title sync looks for by default (spreadsheet_title_pattern), plus .xlsx."""
    return f"Expenses {year}.xlsx"


def write_template(out: Path, year: int | None = None, force: bool = False,
                   currency_code: str = "INR") -> Path:
    """Write the template workbook for `year` (default: this year) to `out`; returns `out`.
    The amount column is headed Expense(<currency_code>)."""
    year = year or date.today().year
    if not 1900 <= year <= 9999:
        raise ExpenseTrackerError(f"Year must be between 1900 and 9999, not {year}")
    if out.exists() and not force:
        raise ExpenseTrackerError(f"{out} already exists; pass --force to overwrite it.")
    suffix = f"{year % 100:02d}"
    code = re.sub(r"[^A-Za-z]", "", currency_code or "")[:3].upper()
    header = f"Expense({code})" if code else "Expense"
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(TEMPLATE_XLSX) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename.endswith((".xml", ".rels")) and (year != TEMPLATE_YEAR or header != "Expense(INR)"):
                text = MONTH_TAB.sub(lambda m: m.group(1) + suffix, data.decode("utf-8"))
                data = text.replace(">Expense(INR)<", f">{header}<").encode("utf-8")
            dst.writestr(item, data)
    return out
