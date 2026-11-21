"""Build the dashboard HTML (a single file with the data and Chart.js embedded) from SQLite.

Everything the page needs is inlined, so it works offline and makes no third-party requests.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from spendscope import db
from spendscope.budgets import all_budgets
from spendscope.labels import MISSING_LABELS

TEMPLATES = Path(__file__).resolve().parent / "templates"
TEMPLATE = TEMPLATES / "dashboard.html"
CHART_JS = TEMPLATES / "chart.umd.min.js"  # Chart.js 4.4.1, MIT licensed, bundled to work offline


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
        income = con.execute(
            "SELECT date, amount, COALESCE(description,'') FROM income ORDER BY date, id").fetchall()
        last = con.execute("SELECT run_at FROM sync_runs ORDER BY id DESC LIMIT 1").fetchone()
        budgets = all_budgets(con)
    finally:
        con.close()
    return {"rows": [row[1:] + (labels.get(row[0], []), row[0]) for row in rows],
            "budgets": budgets,
            "income": [list(row) for row in income],
            "lastSync": last[0] if last else None,
            "generated": datetime.now().isoformat(timespec="minutes")}


def render_dashboard(data: dict, symbol: str, editable: bool = False, locale: str = "en-IN") -> str:
    """Render the dashboard, optionally enabling local label editing."""
    data = {**data, "currency": symbol, "locale": locale, "editable": editable,
            "missingLabels": list(MISSING_LABELS)}
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = TEMPLATE.read_text(encoding="utf-8")
    # Chart.js first, so text in the data can never be taken for its placeholder
    html = html.replace("/*__CHARTJS__*/", CHART_JS.read_text(encoding="utf-8"), 1)
    return html.replace("/*__DATA__*/null", payload, 1)


def write_report(db_path: Path, symbol: str, out: Path, locale: str = "en-IN") -> int:
    """Write a static, view-only dashboard; returns the number of expenses in it."""
    data = load(db_path)
    out.write_text(render_dashboard(data, symbol, locale=locale), encoding="utf-8")
    return len(data["rows"])
