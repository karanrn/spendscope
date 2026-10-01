#!/usr/bin/env python3
"""
Build an interactive expense dashboard (a single HTML file) from the SQLite DB.

    python report.py                      # writes dashboard.html next to the DB
    python report.py --open               # ...and opens it in your browser
    python report.py --out ~/Desktop/expenses.html

Standard library only. The page loads Chart.js from a CDN, so viewing it
needs an internet connection; the data itself is embedded in the file.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tomllib
import webbrowser
from datetime import datetime
from pathlib import Path


def load(db: Path) -> dict:
    if not db.exists():
        sys.exit(f"No database at {db}. Run sync.py first.")
    con = sqlite3.connect(db)
    rows = con.execute(
        "SELECT id, date, amount, category, COALESCE(payment,''), COALESCE(description,''),"
        " COALESCE(subcategory,'') FROM expenses ORDER BY date, id").fetchall()
    labels: dict[int, list[str]] = {}
    for expense_id, label in con.execute(
            "SELECT expense_id, label FROM expense_labels ORDER BY label COLLATE NOCASE"):
        labels.setdefault(expense_id, []).append(label)
    last = con.execute("SELECT run_at FROM sync_runs ORDER BY id DESC LIMIT 1").fetchone()
    con.close()
    return {"rows": [row[1:] + (labels.get(row[0], []), row[0]) for row in rows],
            "lastSync": last[0] if last else None,
            "generated": datetime.now().isoformat(timespec="minutes")}


def render_dashboard(db: Path, symbol: str, editable: bool = False) -> str:
    """Render the standalone dashboard, optionally enabling local label editing."""
    data = load(db)
    data["currency"] = symbol
    data["editable"] = editable
    template = (Path(__file__).resolve().parent / "dashboard_template.html").read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return template.replace("/*__DATA__*/null", payload)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--out", help="output HTML path (default: dashboard.html next to the DB)")
    ap.add_argument("--open", action="store_true", help="open the dashboard in a browser")
    a = ap.parse_args()

    cfg_path = Path(a.config).resolve()
    cfg = tomllib.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
    db = Path(cfg.get("database", {}).get("path", "expenses.db"))
    db = db if db.is_absolute() else cfg_path.parent / db
    symbol = cfg.get("parsing", {}).get("currency_symbol", "₹")

    html = render_dashboard(db, symbol)

    out = Path(a.out).expanduser() if a.out else db.parent / "dashboard.html"
    out.write_text(html, encoding="utf-8")
    print(f"Dashboard written: {out}  ({len(load(db)['rows'])} expenses)")
    if a.open:
        webbrowser.open(out.resolve().as_uri())


if __name__ == "__main__":
    main()
