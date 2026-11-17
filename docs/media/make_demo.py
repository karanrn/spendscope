"""Build a demo project with made-up data: the template workbook filled for Jan–Oct 2026, a bank
statement for October, a config, and a copy of the code.

The demo is pinned to DEMO_TODAY: data runs to the day before, the statement covers that month,
and demo.json passes the date on so the browser clock can be set to it.

usage: make_demo.py <repo> <out-dir>
"""
import json, random, shutil, sys
from datetime import date, datetime, timedelta
from pathlib import Path
from openpyxl import load_workbook

repo, out = Path(sys.argv[1]), Path(sys.argv[2])
if out.exists():
    shutil.rmtree(out)
out.mkdir(parents=True)
for name in ("expense_tracker", ):
    shutil.copytree(repo / name, out / name, ignore=shutil.ignore_patterns("__pycache__"))
for name in ("Makefile", "sync.py", "serve.py", "report.py", "labels.py"):
    shutil.copy(repo / name, out / name)
(out / "config.toml").write_text('''[source]
type = "file"
file_path = "Expenses 2026.xlsx"
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
''', encoding="utf-8")

rnd = random.Random(2026)
DEMO_TODAY = date(2026, 10, 23)
END = DEMO_TODAY - timedelta(days=1)
rows = []  # (date, amount, item, label)

def add(d, amt, item, label):
    if d <= END:
        rows.append((d, amt, item, label))

def r10(lo, hi):
    return rnd.randrange(lo, hi + 1, 10)

for m in range(1, 11):
    first = date(2026, m, 1)
    days = ((first.replace(month=m % 12 + 1) if m < 12 else date(2027, 1, 1)) - first).days
    D = lambda n: date(2026, m, min(n, days))
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
wb = load_workbook(repo / "Template - Expenses 2026.xlsx")
tabs = ["Jan26", "Feb26", "Mar26", "Apr26", "May26", "Jun26", "Jul26", "Aug26", "Sep26", "Oct26"]
for i, tab in enumerate(tabs, start=1):
    ws, r = wb[tab], 2
    for d, amt, item, label in (x for x in rows if x[0].month == i):
        ws.cell(r, 1, datetime(d.year, d.month, d.day)).number_format = "dd/mm/yyyy"
        ws.cell(r, 2, amt); ws.cell(r, 3, item); ws.cell(r, 4, label)
        r += 1
wb.save(out / "Expenses 2026.xlsx")

# Bank statement for 1–22 Oct in the bank's comma-delimited .txt layout. Some withdrawals
# are already in the sheet (same date and amount); the rest arrive without a Label.
oct_sheet = {(d, amt): item for d, amt, item, _ in rows if d.month == 10}
txns = [(date(2026, 10, 1), "SALARY CREDIT-ACME TECH PVT LTD", 0, 165000)]
for (d, amt), item in sorted(oct_sheet.items()):
    if item in ("Rent", "SIP - index fund", "Electricity bill", "Internet", "Diwali shopping", "DMart monthly stock-up"):
        narration = {"Rent": "NEFT DR-LANDLORD-RENT OCT", "SIP - index fund": "ACH D- ICICI PRU MF-SIP",
                     "Electricity bill": "BILLPAY-BESCOM", "Internet": "BILLPAY-ACT FIBERNET",
                     "Diwali shopping": "POS 400000XXXXXX0001 LIFESTYLE", "DMart monthly stock-up": "POS 400000XXXXXX0001 DMART"}[item]
        txns.append((d, narration, amt, 0))
txns += [
    (date(2026, 10, 4), "UPI-GROWW-ADD MONEY-PAYMENT", 5000, 0),
    (date(2026, 10, 7), "ATM WDL-ATM CASH KORAMANGALA", 3000, 0),
    (date(2026, 10, 9), "UPI-ZEPTO MARKETPLACE-ORDER", 742.5, 0),
    (date(2026, 10, 11), "UPI-A FRIEND-SPLIT DINNER", 0, 1200),
    (date(2026, 10, 13), "POS 400000XXXXXX0001 AMAZON PAY", 1299, 0),
    (date(2026, 10, 15), "NEFT DR-PRESTIGE SOCIETY-MAINTENANCE", 2500, 0),
    (date(2026, 10, 18), "UPI-GROWW-ADD MONEY-PAYMENT", 5000, 0),
    (date(2026, 10, 19), "UPI-BLINKIT-ORDER", 389, 0),
    (date(2026, 10, 21), "UPI-APOLLO PHARMACY", 612, 0),
]
txns.sort(key=lambda t: t[0])
bal, ref = 42000.0, 4410000000000101
lines = ["", "  Date     ,Narration                              ,Value Dat,Debit Amount       ,Credit Amount      ,Chq/Ref Number   ,Closing Balance"]
for d, narr, dr, cr in txns:
    bal += cr - dr
    ref += 1
    ds = d.strftime("%d/%m/%y")
    lines.append(f" {ds}  ,{narr:<39},{ds} ,{dr:>19.2f},{cr:>19.2f},{ref:<17},{bal:>15.2f}")
(out / "Acct_Statement_XXXXXXXX1234_22102026.txt").write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
print(len(rows), "sheet rows;", len(txns), "statement rows")
(out / "demo.json").write_text(json.dumps({"today": DEMO_TODAY.isoformat()}))
