"""Run the demo commands for real and save their output for the terminal scene.

usage: capture.py <demo-dir> <out.json>   (starts from an empty database)
"""
import json, os, subprocess, sys
from pathlib import Path
demo, out = Path(sys.argv[1]), Path(sys.argv[2])
for f in demo.glob("expenses.db*"):
    f.unlink()
B = "python -m expense_tracker budgets"
steps = [
    ("make dry-run", "Preview the import from “Expenses 2026”: nothing is written"),
    ("make sync", "Import every month tab into SQLite (expenses.db)"),
    ("make statement FILES='Acct_Statement_*.txt'", "Import bank-statement withdrawals; ones already in the sheet are skipped"),
    ("python labels.py add Work --source-label Food --item-contains 'team lunch'", "Bulk-add your own extra labels on top of the sheet’s Label"),
    ("python labels.py add Trip --source-label Misc --item-contains goa", "Bulk-add your own extra labels on top of the sheet’s Label"),
    ("python labels.py set-primary Investment --item-contains groww", "Give bank rows without a Label one in bulk"),
    (f"{B} set 'Rent And Bills' 35000", "Set a monthly budget per Label"),
    (f"{B} set Food 12000", "Set a monthly budget per Label"),
    (f"{B} set Grocery 7000", "Set a monthly budget per Label"),
    (f"{B} set Misc 6000", "Set a monthly budget per Label"),
    (f"{B} set Petrol 6000", "Set a monthly budget per Label"),
    (f"{B} list --month 2026-10", "This month’s spend against each budget"),
]
# commands run with this interpreter as python3 (so make's echoed commands read as usual); drop
# PYTHON and the outer make's flags, which would otherwise pass `make media PYTHON=…` through
env = {k: v for k, v in os.environ.items() if k not in ("PYTHON", "MAKEFLAGS", "MAKEOVERRIDES", "MFLAGS", "MAKELEVEL")}
env["PATH"] = os.path.dirname(sys.executable) + os.pathsep + env["PATH"]
result = []
for cmd, caption in steps:
    run = cmd.replace("python ", "python3 ", 1) if cmd.startswith("python ") else cmd
    p = subprocess.run(run, shell=True, cwd=demo, capture_output=True, text=True, env=env)
    if p.returncode:
        sys.exit(f"{cmd} failed:\n{p.stderr}")
    text = (p.stdout + p.stderr).replace(str(demo), "~/expense-tracker")
    result.append({"cmd": cmd, "out": text.rstrip("\n").split("\n"), "caption": caption})
result.append({"cmd": "make serve", "out": ["python3 -m expense_tracker serve",
               "Editable expense dashboard: http://127.0.0.1:8765/ (Ctrl+C to stop)"],
               "caption": "Start the editable dashboard on localhost"})
out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
for s in result:
    print("$", s["cmd"]); print("\n".join(s["out"]))
