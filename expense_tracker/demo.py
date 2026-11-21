"""Made-up demo data: a filled-in yearly workbook per year, a bank statement for the latest
month, and a config. `demo` imports it and opens the dashboard, so the tool can be tried
before setting anything up; `make media` (docs/media/make_demo.py) records the README video
from the same data, pinned to one date.
"""
from __future__ import annotations

import random
import shutil
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

from expense_tracker import ExpenseTrackerError
from expense_tracker.template import workbook_name, write_template

MARKER = ".expense-tracker-demo"  # marks a folder build_demo may replace

CONFIG = '''[source]
type = "file"
file_path = "{file_path}"
file_sheet = ""
skip_worksheets = ["Summary"]

[database]
path = "expenses.db"

[columns]
date        = "Date"
amount      = "Expense"
category    = "Label"
description = "Item"
payment     = ""
subcategory = ""

[parsing]
date_formats = ["%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y"]
currency_symbol = "₹"
failures_log = "parse_failures.csv"
'''
EXTRAS = '''
[statements]
import_deposits = true

[rules]
Grocery = ["zepto", "blinkit"]
Essential = ["pharmacy"]
Investment = ["groww"]
'''
STATEMENT_NAME = "Acct_Statement_XXXXXXXX1234_{:%d%m%Y}.txt"
NARRATIONS = {"Rent": "NEFT DR-LANDLORD-RENT", "SIP - index fund": "ACH D- ICICI PRU MF-SIP",
              "Electricity bill": "BILLPAY-BESCOM", "Internet": "BILLPAY-ACT FIBERNET",
              "Diwali shopping": "POS 400000XXXXXX0001 LIFESTYLE",
              "DMart monthly stock-up": "POS 400000XXXXXX0001 DMART"}


def months_until(end: date, count: int | None) -> list[tuple[int, int]]:
    """(year, month) of the `count` months up to end's month, or January to it when None."""
    y, m = end.year, end.month
    out = []
    while (count is None and y == end.year) or (count is not None and len(out) < count):
        out.append((y, m))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out[::-1]


def sheet_rows(end: date, months: list[tuple[int, int]], rnd: random.Random) -> list[tuple]:
    """(date, amount, item, label) for every month, none after `end`."""
    rows = []

    def add(d, amt, item, label):
        if d <= end:
            rows.append((d, amt, item, label))

    def r10(lo, hi):
        return rnd.randrange(lo, hi + 1, 10)

    for y, m in months:
        first = date(y, m, 1)
        days = ((first.replace(month=m + 1) if m < 12 else date(y + 1, 1, 1)) - first).days
        D = lambda n: date(y, m, min(n, days))
        add(D(1), 28000, "Rent", "Rent And Bills")
        add(D(1), 3000, "Maid salary", "Rent And Bills")
        add(D(5), r10(1400, 2600), "Electricity bill", "Rent And Bills")
        add(D(10), 999, "Internet", "Rent And Bills")
        add(D(15), 399, "Mobile recharge", "Rent And Bills")
        add(D(5), 15000, "SIP - index fund", "Investment")
        if m in (3, 9):
            add(D(20), 40000, "Gold ETF" if m == 3 else "PPF deposit", "Investment")
        add(D(2), r10(2600, 3600), "DMart monthly stock-up", "Grocery")
        add(D(3), 1800, "Milk (monthly)", "Grocery")
        for wk in range(4):
            add(D(6 + wk * 7 + rnd.randint(0, 2)), r10(350, 900), "Vegetables and fruits", "Grocery")
        if rnd.random() < .6:
            add(D(rnd.randint(12, 26)), r10(700, 1600), "BigBasket order", "Grocery")
        for d in range(rnd.randint(2, 5), days + 1, rnd.randint(9, 11)):
            add(D(d), r10(1500, 2100), "Petrol", "Petrol")
        for d in range(1, days + 1):
            p = rnd.random()
            if p < .30:
                add(D(d), r10(250, 800), "Coffee and sandwich", "Food")
            elif p < .48:
                add(D(d), r10(280, 820), rnd.choice(["Swiggy - thali", "Swiggy - thali", "Swiggy - biryani", "Swiggy - dosa breakfast"]), "Food")
            elif p < .56:
                add(D(d), r10(350, 900), rnd.choice(["Zomato - burgers", "Zomato - pizza"]), "Food")
            elif p < .62:
                add(D(d), r10(120, 260), "Chai and samosa", "Food")
            if rnd.random() < .05:
                add(D(d), r10(900, 2400), "Dinner out with friends", "Food")
            if d % 7 == 3 and rnd.random() < .5:
                add(D(d), r10(450, 950), "Team lunch", "Food")
            if rnd.random() < .07:
                add(D(d), r10(150, 600), "Uber ride", "Misc")
        for _ in range(rnd.randint(1, 3)):
            item, lo, hi = rnd.choice([("Movie tickets", 500, 1100), ("Haircut", 300, 500), ("Amazon order", 400, 2500), ("Birthday gift", 800, 2500)])
            add(D(rnd.randint(1, days)), r10(lo, hi), item, "Misc")
        if m == 9:
            add(D(14), 14500, "Goa trip - flights", "Misc")
            add(D(16), 9200, "Goa trip - hotel", "Misc")
            add(D(22), 2450, "Gym shoes", "Misc")
        if m == 10:
            add(D(12), 6800, "Diwali shopping", "Misc")
            add(D(18), 4200, "Diwali sweets and gifts", "Misc")
        for _ in range(rnd.randint(1, 3)):
            item, lo, hi = rnd.choice([("Pharmacy", 200, 1400), ("Doctor visit", 500, 900), ("Toiletries", 300, 800)])
            add(D(rnd.randint(1, days)), r10(lo, hi), item, "Essential")
    rows.sort()
    return rows


def write_workbooks(out: Path, rows: list[tuple]) -> list[Path]:
    """One `Expenses YYYY.xlsx` per year, from the template, with the rows on their month tabs."""
    from openpyxl import load_workbook
    paths = []
    for year in sorted({d.year for d, *_ in rows}):
        path = write_template(out / workbook_name(year), year, force=True)
        wb = load_workbook(path)
        for month in range(1, 13):
            ws, r = wb[date(year, month, 1).strftime("%b%y")], 2
            for d, amt, item, label in (x for x in rows if (x[0].year, x[0].month) == (year, month)):
                ws.cell(r, 1, datetime(d.year, d.month, d.day)).number_format = "dd/mm/yyyy"
                ws.cell(r, 2, amt)
                ws.cell(r, 3, item)
                ws.cell(r, 4, label)
                r += 1
        wb.save(path)
        paths.append(path)
    return paths


def write_statement(out: Path, end: date, rows: list[tuple]) -> Path:
    """A bank statement for end's month in the bank's comma-delimited .txt layout. Some
    withdrawals are already in the sheet (same date and amount); the rest have no Label."""
    y, m = end.year, end.month
    in_sheet = {(d, amt): item for d, amt, item, _ in rows if (d.year, d.month) == (y, m)}
    txns = [(date(y, m, 1), "SALARY CREDIT-ACME TECH PVT LTD", 0, 165000)]
    for (d, amt), item in sorted(in_sheet.items()):
        if item in NARRATIONS:
            narration = NARRATIONS[item] + (f" {d:%b}".upper() if item == "Rent" else "")
            txns.append((d, narration, amt, 0))
    extra = [(4, "UPI-GROWW-ADD MONEY-PAYMENT", 5000, 0), (7, "ATM WDL-ATM CASH KORAMANGALA", 3000, 0),
             (9, "UPI-ZEPTO MARKETPLACE-ORDER", 742.5, 0), (11, "UPI-A FRIEND-SPLIT DINNER", 0, 1200),
             (13, "POS 400000XXXXXX0001 AMAZON PAY", 1299, 0), (15, "NEFT DR-PRESTIGE SOCIETY-MAINTENANCE", 2500, 0),
             (18, "UPI-GROWW-ADD MONEY-PAYMENT", 5000, 0), (19, "UPI-BLINKIT-ORDER", 389, 0),
             (21, "UPI-APOLLO PHARMACY", 612, 0)]
    txns += [(date(y, m, day), narr, dr, cr) for day, narr, dr, cr in extra]
    txns = sorted((t for t in txns if t[0] <= end), key=lambda t: t[0])
    bal, ref = 42000.0, 4410000000000101
    lines = ["", "  Date     ,Narration                              ,Value Dat,Debit Amount       ,Credit Amount      ,Chq/Ref Number   ,Closing Balance"]
    for d, narr, dr, cr in txns:
        bal += cr - dr
        ref += 1
        ds = d.strftime("%d/%m/%y")
        lines.append(f" {ds}  ,{narr:<39},{ds} ,{dr:>19.2f},{cr:>19.2f},{ref:<17},{bal:>15.2f}")
    path = out / STATEMENT_NAME.format(end)
    path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    return path


def build_demo(out: Path, today: date | None = None, months: int | None = 12,
               extras: bool = True) -> dict:
    """Write the demo project into `out` (replacing it); data runs to the day before `today`.

    months=None covers January to today's month only, as the README media do. extras turns on
    income from deposits and a few [rules]. Returns the paths written.
    """
    today = today or date.today()
    end = today - timedelta(days=1)
    if out.exists() and any(out.iterdir()):
        if not (out / MARKER).exists():
            raise ExpenseTrackerError(f"{out} is not empty and is not an earlier demo; choose another --dir.")
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / MARKER).write_text("Made-up data written by `python -m expense_tracker demo`; safe to delete.\n")
    rows = sheet_rows(end, months_until(end, months), random.Random(2026))
    workbooks = write_workbooks(out, rows)
    file_path = workbooks[0].name if len(workbooks) == 1 else "Expenses *.xlsx"
    config = out / "config.toml"
    config.write_text(CONFIG.format(file_path=file_path) + (EXTRAS if extras else ""), encoding="utf-8")
    statement = write_statement(out, end, rows)
    return {"config": config, "workbooks": workbooks, "statement": statement, "rows": len(rows)}


def run_demo(directory: Path | None = None, serve: bool = True, open_browser: bool = True,
             port: int = 8765) -> Path:
    """Build the demo, import it, set a few budgets, and serve the dashboard; returns the folder."""
    from expense_tracker import budgets, db
    from expense_tracker.config import currency_symbol, database_path, display_locale, load_config
    from expense_tracker.sync import sync, sync_statements

    directory = directory or Path(tempfile.gettempdir()) / "expense-tracker-demo"
    built = build_demo(directory)
    cfg = load_config(built["config"])
    sync(cfg)
    sync_statements(cfg, [built["statement"]])
    con = db.connect(database_path(cfg))
    try:
        for label, amount in (("Rent And Bills", 35000), ("Food", 12000), ("Grocery", 7000),
                              ("Misc", 6000), ("Petrol", 6000)):
            budgets.set_budget(con, label, amount)
    finally:
        con.close()
    print(f"\nDemo project with made-up data: {directory}")
    print(f"Try commands against it with --config '{built['config']}'")
    if serve:
        from expense_tracker.server import serve as serve_dashboard
        serve_dashboard(database_path(cfg), currency_symbol(cfg), "127.0.0.1", port, open_browser,
                        display_locale(cfg))
    return directory
