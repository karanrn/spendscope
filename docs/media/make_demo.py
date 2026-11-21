"""Build the README media's demo project: expense_tracker.demo's made-up data for January to
October 2026 (no income or rules, so the recorded commands show the basics), plus a copy of the
code so the recorded commands run as they would in a checkout.

The demo is pinned to DEMO_TODAY: data runs to the day before, the statement covers that month,
and demo.json passes the date on so the browser clock can be set to it.

usage: make_demo.py <repo> <out-dir>
"""
import json, shutil, sys
from datetime import date
from pathlib import Path

repo, out = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(repo))
from expense_tracker.demo import build_demo  # noqa: E402

DEMO_TODAY = date(2026, 10, 23)
built = build_demo(out, DEMO_TODAY, months=None, extras=False)
shutil.copytree(repo / "expense_tracker", out / "expense_tracker", ignore=shutil.ignore_patterns("__pycache__"))
for name in ("Makefile", "sync.py", "serve.py", "report.py", "labels.py"):
    shutil.copy(repo / name, out / name)
print(built["rows"], "sheet rows")
(out / "demo.json").write_text(json.dumps({"today": DEMO_TODAY.isoformat()}))
