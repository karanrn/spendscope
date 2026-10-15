"""Build the dashboard HTML (a single file with the data embedded) from SQLite.

The page loads Chart.js from a CDN, so viewing it needs an internet connection.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from expense_tracker import db
from expense_tracker.labels import MISSING_LABELS

TEMPLATE = Path(__file__).resolve().parent / "templates" / "dashboard.html"


def load(db_path: Path) -> dict:
    """Everything the dashboard page needs, in the compact shape its script expects."""
    con = db.connect(db_path)
    try:
        rows = con.execute(
            "SELECT id, date, amount, category, COALESCE(payment,''), COALESCE(description,''),"
            " COALESCE(subcategory,'') FROM expenses ORDER BY date, id").fetchall()
        labels: dict[int, list[str]] = {}
        for expense_id, label in con.execute(
                "SELECT expense_id, label FROM expense_labels ORDER BY label COLLATE NOCASE"):
            labels.setdefault(expense_id, []).append(label)
        last = con.execute("SELECT run_at FROM sync_runs ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        con.close()
    return {"rows": [row[1:] + (labels.get(row[0], []), row[0]) for row in rows],
            "lastSync": last[0] if last else None,
            "generated": datetime.now().isoformat(timespec="minutes")}


def render_dashboard(data: dict, symbol: str, editable: bool = False) -> str:
    """Render the dashboard, optionally enabling local label editing."""
    data = {**data, "currency": symbol, "editable": editable, "missingLabels": list(MISSING_LABELS)}
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", payload)


def write_report(db_path: Path, symbol: str, out: Path) -> int:
    """Write a static, view-only dashboard; returns the number of expenses in it."""
    data = load(db_path)
    out.write_text(render_dashboard(data, symbol), encoding="utf-8")
    return len(data["rows"])
