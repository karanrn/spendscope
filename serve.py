#!/usr/bin/env python3
"""Run the dashboard locally with SQLite-backed custom-label editing.

    python serve.py                 # opens http://127.0.0.1:8765
    python serve.py --no-open
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import tomllib
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from report import render_dashboard
from sync import initialize_database


def configured_values(config: str) -> tuple[Path, str]:
    config_path = Path(config).resolve()
    if not config_path.exists():
        raise SystemExit(f"Config not found: {config_path}")
    cfg = tomllib.loads(config_path.read_text(encoding="utf-8"))
    db = Path(cfg.get("database", {}).get("path", "expenses.db"))
    db_path = db if db.is_absolute() else config_path.parent / db
    currency = cfg.get("parsing", {}).get("currency_symbol", "₹")
    return db_path, currency


def make_handler(db_path: Path, currency: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: dict) -> None:
            self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def do_GET(self) -> None:
            if urlparse(self.path).path not in ("/", "/index.html"):
                self._json(404, {"error": "Not found"})
                return
            try:
                html = render_dashboard(db_path, currency, editable=True).encode("utf-8")
            except SystemExit as exc:
                self._json(500, {"error": str(exc)})
                return
            self._send(200, html, "text/html; charset=utf-8")

        def do_POST(self) -> None:
            if urlparse(self.path).path != "/api/labels":
                self._json(404, {"error": "Not found"})
                return
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                if content_length < 1 or content_length > 8192:
                    self._json(400, {"error": "Request body must be between 1 and 8192 bytes"})
                    return
                payload = json.loads(self.rfile.read(content_length))
                if not isinstance(payload, dict):
                    raise ValueError("Request body must be a JSON object")
                expense_id = payload.get("expense_id")
                label = payload.get("label", "").strip()
                action = payload.get("action")
                if isinstance(expense_id, bool) or not isinstance(expense_id, int) or expense_id < 1:
                    raise ValueError("Invalid expense ID")
                if not label or len(label) > 80 or any(ord(c) < 32 for c in label):
                    raise ValueError("Label must contain 1–80 printable characters")
                if action not in ("add", "remove"):
                    raise ValueError("Action must be 'add' or 'remove'")

                with sqlite3.connect(db_path, timeout=5) as con:
                    con.execute("PRAGMA foreign_keys = ON")
                    con.execute("PRAGMA busy_timeout = 5000")
                    initialize_database(con)
                    if not con.execute("SELECT 1 FROM expenses WHERE id = ?", (expense_id,)).fetchone():
                        self._json(404, {"error": "Expense not found"})
                        return
                    if action == "add":
                        con.execute(
                            "INSERT OR IGNORE INTO expense_labels(expense_id, label, created_at) "
                            "VALUES (?, ?, ?)",
                            (expense_id, label, datetime.now().isoformat(timespec="seconds")),
                        )
                    else:
                        con.execute(
                            "DELETE FROM expense_labels WHERE expense_id = ? AND label = ? COLLATE NOCASE",
                            (expense_id, label),
                        )
                    labels = [row[0] for row in con.execute(
                        "SELECT label FROM expense_labels WHERE expense_id = ? ORDER BY label COLLATE NOCASE",
                        (expense_id,),
                    )]
                    all_labels = [row[0] for row in con.execute(
                        "SELECT DISTINCT label FROM expense_labels ORDER BY label COLLATE NOCASE"
                    )]
                self._json(200, {"expense_id": expense_id, "labels": labels, "all_labels": all_labels})
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError, sqlite3.Error) as exc:
                self._json(400, {"error": str(exc)})

        def log_message(self, fmt: str, *args: object) -> None:
            print(f"{self.log_date_time_string()} {fmt % args}")

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default: localhost only)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true", help="do not open the browser automatically")
    args = parser.parse_args()

    db_path, currency = configured_values(args.config)
    if not db_path.exists():
        raise SystemExit(f"No database at {db_path}. Run sync.py first.")
    server = ThreadingHTTPServer((args.host, args.port), make_handler(db_path, currency))
    url = f"http://{args.host}:{args.port}/"
    print(f"Editable expense dashboard: {url} (Ctrl+C to stop)")
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping dashboard server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
