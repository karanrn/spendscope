"""Local dashboard server with an API for editing additional labels, missing Labels and budgets."""
from __future__ import annotations

import errno
import json
import sqlite3
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from expense_tracker import ExpenseTrackerError, budgets, db, labels
from expense_tracker.report import load, render_dashboard

MAX_BODY = 8192


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
                html = render_dashboard(load(db_path), currency, editable=True).encode("utf-8")
            except ExpenseTrackerError as exc:
                self._json(500, {"error": str(exc)})
                return
            self._send(200, html, "text/html; charset=utf-8")

        def do_POST(self) -> None:
            routes = {"/api/labels": self._post_labels, "/api/budgets": self._post_budgets}
            handler = routes.get(urlparse(self.path).path)
            if handler is None:
                self._json(404, {"error": "Not found"})
                return
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                if content_length < 1 or content_length > MAX_BODY:
                    self._json(400, {"error": f"Request body must be between 1 and {MAX_BODY} bytes"})
                    return
                payload = json.loads(self.rfile.read(content_length))
                if not isinstance(payload, dict):
                    raise ValueError("Request body must be a JSON object")
                handler(payload)
            except (ValueError, ExpenseTrackerError, UnicodeDecodeError, sqlite3.Error) as exc:
                self._json(400, {"error": str(exc)})

        def _post_budgets(self, payload: dict) -> None:
            """Set a Label's budget, or remove it when amount is null."""
            label, amount, month = payload.get("label"), payload.get("amount"), payload.get("month", "")
            if not isinstance(label, str):
                raise ValueError("Label must be a string")
            if not isinstance(month, str):
                raise ValueError("Month must be a string")
            con = db.connect(db_path)
            try:
                if amount is None:
                    budgets.remove_budget(con, label, month)
                else:
                    label = budgets.set_budget(con, label, amount, month)
                body = {"label": label, "budgets": budgets.all_budgets(con)}
            finally:
                con.close()
            self._json(200, body)

        def _post_labels(self, payload: dict) -> None:
            expense_id = payload.get("expense_id")
            action = payload.get("action")
            if isinstance(expense_id, bool) or not isinstance(expense_id, int) or expense_id < 1:
                raise ValueError("Invalid expense ID")
            if not isinstance(payload.get("label", ""), str):
                raise ValueError("Label must be a string")
            label = labels.clean_label(payload.get("label", ""))
            if action not in ("add", "remove", "set_primary"):
                raise ValueError("Action must be 'add', 'remove' or 'set_primary'")

            con = db.connect(db_path)
            try:
                if not labels.expense_exists(con, expense_id):
                    self._json(404, {"error": "Expense not found"})
                    return
                if action == "set_primary":
                    if not labels.set_primary_label(con, [expense_id], label):
                        self._json(409, {"error": "This expense already has a Label"})
                        return
                else:
                    change = labels.add_label if action == "add" else labels.remove_label
                    change(con, [expense_id], label)
                body = {"expense_id": expense_id, "labels": labels.labels_for(con, expense_id),
                        "all_labels": labels.all_labels(con),
                        "category": labels.primary_label(con, expense_id)}
            finally:
                con.close()
            self._json(200, body)

        def log_message(self, fmt: str, *args: object) -> None:
            print(f"{self.log_date_time_string()} {fmt % args}")

    return Handler


def dashboard_running(url: str) -> bool:
    """True if the server at url answers like this dashboard."""
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            return b"Expense Dashboard" in response.read(4096)
    except OSError:
        return False


def serve(db_path: Path, currency: str, host: str, port: int, open_browser: bool = True) -> None:
    if not db_path.exists():
        raise ExpenseTrackerError(f"No database at {db_path}. Run sync.py first.")
    url = f"http://{host}:{port}/"
    try:
        server = ThreadingHTTPServer((host, port), make_handler(db_path, currency))
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE or not dashboard_running(url):
            raise
        # A dashboard is already being served here: just show it again.
        print(f"Dashboard already running at {url}")
        if open_browser:
            webbrowser.open_new_tab(url)
        return
    print(f"Editable expense dashboard: {url} (Ctrl+C to stop)")
    if open_browser:
        webbrowser.open_new_tab(url)  # a tab in the existing browser window, not a new window
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping dashboard server.")
    finally:
        server.server_close()
