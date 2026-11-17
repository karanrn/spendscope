import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from expense_tracker import ExpenseTrackerError, db, labels
from expense_tracker.cli import main
from expense_tracker.config import database_path
from expense_tracker.server import make_handler
from expense_tracker.sync import sync, sync_statements
from tests.helpers import Project, quietly, write_statements


class LabelTests(unittest.TestCase):
    def setUp(self):
        self.project = Project()
        quietly(sync, self.project.config())
        self.con = db.connect(database_path(self.project.config()))

    def tearDown(self):
        self.con.close()
        self.project.cleanup()

    def test_find_add_remove_report_actual_changes(self):
        ids = labels.find_expenses(self.con, "grocery", item_contains="GROC")
        self.assertEqual(len(ids), 2)
        self.assertEqual(labels.add_label(self.con, ids, "Home"), 2)
        self.assertEqual(labels.add_label(self.con, ids, "home"), 0)  # case-insensitive duplicate
        self.assertEqual(labels.label_counts(self.con), [("Home", 2)])
        self.assertEqual(labels.remove_label(self.con, ids[:1], "HOME"), 1)
        self.assertEqual(labels.remove_label(self.con, ids[:1], "Home"), 0)

    def test_date_filters(self):
        self.assertEqual(len(labels.find_expenses(self.con, "Food", from_date="2026-01-02")), 0)
        self.assertEqual(len(labels.find_expenses(self.con, "Food", to_date="2026-01-01")), 1)

    def test_label_validation(self):
        self.assertEqual(labels.clean_label("  Work "), "Work")
        for bad in ("", "   ", "x" * 81, "tab\there"):
            with self.assertRaises(ExpenseTrackerError):
                labels.clean_label(bad)

    def test_cli_accepts_config_before_or_after_action(self):
        config = str(self.project.config_path)
        _, out = quietly(main, ["labels", "--config", config, "add", "Trip", "--source-label", "Food"])
        self.assertIn("Added 'Trip' on 1 of 1", out)
        _, out = quietly(main, ["labels", "list", "--config", config])
        self.assertIn("Trip: 1 expenses", out)


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.project = Project()
        cfg = self.project.config()
        quietly(sync, cfg)
        quietly(sync_statements, cfg, list(write_statements(self.project.root)))  # adds expense 4, unlabeled
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(database_path(cfg), "₹"))
        self.server.RequestHandlerClass.log_message = lambda *args: None
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.project.cleanup()

    def post(self, payload, path="/api/labels"):
        req = urllib.request.Request(f"{self.base}{path}", data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as res:
                return res.status, json.load(res)
        except urllib.error.HTTPError as err:
            with err:
                return err.code, json.load(err)

    def test_dashboard_page_embeds_editable_data(self):
        with urllib.request.urlopen(self.base + "/") as res:
            html = res.read().decode()
        self.assertIn("Expense Dashboard", html)
        self.assertIn('"editable":true', html)
        self.assertIn("Coffee and sandwich", html)
        self.assertIn('"missingLabels":["","Uncategorized"]', html)

    def test_add_and_remove_label(self):
        status, body = self.post({"expense_id": 1, "label": " Work ", "action": "add"})
        self.assertEqual((status, body["labels"], body["all_labels"]), (200, ["Work"], ["Work"]))
        status, body = self.post({"expense_id": 1, "label": "work", "action": "remove"})
        self.assertEqual((status, body["labels"]), (200, []))

    def test_set_primary_label_only_when_missing(self):
        status, body = self.post({"expense_id": 4, "label": "Investment", "action": "set_primary"})
        self.assertEqual((status, body["category"]), (200, "Investment"))
        self.assertEqual(self.post({"expense_id": 4, "label": "Other", "action": "set_primary"})[0], 409)
        self.assertEqual(self.post({"expense_id": 1, "label": "Other", "action": "set_primary"})[0], 409)

    def test_set_and_remove_budget(self):
        status, body = self.post({"label": "food", "amount": 400}, "/api/budgets")
        self.assertEqual((status, body["label"], body["budgets"]), (200, "Food", [["Food", "", 400.0]]))
        status, body = self.post({"label": "Food", "amount": 900, "month": "2026-01"}, "/api/budgets")
        self.assertEqual(body["budgets"], [["Food", "", 400.0], ["Food", "2026-01", 900.0]])
        status, body = self.post({"label": "Food", "amount": None}, "/api/budgets")
        self.assertEqual((status, body["budgets"]), (200, [["Food", "2026-01", 900.0]]))
        with urllib.request.urlopen(self.base + "/") as res:
            self.assertIn('"budgets":[["Food","2026-01",900.0]]', res.read().decode())

    def test_bad_budget_requests(self):
        for payload in ({"label": "Food", "amount": 0}, {"label": "Food", "amount": "x"},
                        {"label": 5, "amount": 10}, {"label": "Food", "amount": 10, "month": "2026-1"},
                        {"label": "Food", "amount": 10, "month": 202601}, {"label": "", "amount": 10}):
            self.assertEqual(self.post(payload, "/api/budgets")[0], 400, payload)
        self.assertEqual(self.post({"label": "Food", "amount": 10}, "/api/nope")[0], 404)

    def test_bad_requests(self):
        self.assertEqual(self.post({"expense_id": 999, "label": "X", "action": "add"})[0], 404)
        self.assertEqual(self.post({"expense_id": True, "label": "X", "action": "add"})[0], 400)
        self.assertEqual(self.post({"expense_id": 1, "label": "", "action": "add"})[0], 400)
        self.assertEqual(self.post({"expense_id": 1, "label": 5, "action": "add"})[0], 400)
        self.assertEqual(self.post({"expense_id": 1, "label": "X", "action": "rename"})[0], 400)
        self.assertEqual(self.post(["not", "an", "object"])[0], 400)

    def status(self, path, headers, data=None):
        req = urllib.request.Request(f"{self.base}{path}", data=data, headers=headers)
        try:
            with urllib.request.urlopen(req) as res:
                return res.status
        except urllib.error.HTTPError as err:
            with err:
                return err.code

    def test_rejects_other_hosts_and_cross_site_writes(self):
        port = self.server.server_address[1]
        body = json.dumps({"expense_id": 1, "label": "X", "action": "add"}).encode()
        self.assertEqual(self.status("/", {"Host": f"localhost:{port}"}), 200)
        self.assertEqual(self.status("/", {"Host": f"evil.example:{port}"}), 403)  # DNS rebinding
        for headers in ({"Content-Type": "text/plain"},  # a plain form post, no preflight
                        {"Content-Type": "application/json", "Origin": "http://evil.example"},
                        {"Content-Type": "application/json", "Host": f"evil.example:{port}"}):
            self.assertEqual(self.status("/api/labels", headers, body), 403, headers)
        same_origin = {"Content-Type": "application/json", "Origin": self.base}
        self.assertEqual(self.status("/api/labels", same_origin, body), 200)


if __name__ == "__main__":
    unittest.main()
