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
from expense_tracker.sync import sync
from tests.helpers import Project, quietly


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
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(database_path(cfg), "₹"))
        self.server.RequestHandlerClass.log_message = lambda *args: None
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.project.cleanup()

    def post(self, payload):
        req = urllib.request.Request(f"{self.base}/api/labels", data=json.dumps(payload).encode(),
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

    def test_add_and_remove_label(self):
        status, body = self.post({"expense_id": 1, "label": " Work ", "action": "add"})
        self.assertEqual((status, body["labels"], body["all_labels"]), (200, ["Work"], ["Work"]))
        status, body = self.post({"expense_id": 1, "label": "work", "action": "remove"})
        self.assertEqual((status, body["labels"]), (200, []))

    def test_bad_requests(self):
        self.assertEqual(self.post({"expense_id": 999, "label": "X", "action": "add"})[0], 404)
        self.assertEqual(self.post({"expense_id": True, "label": "X", "action": "add"})[0], 400)
        self.assertEqual(self.post({"expense_id": 1, "label": "", "action": "add"})[0], 400)
        self.assertEqual(self.post({"expense_id": 1, "label": 5, "action": "add"})[0], 400)
        self.assertEqual(self.post({"expense_id": 1, "label": "X", "action": "rename"})[0], 400)
        self.assertEqual(self.post(["not", "an", "object"])[0], 400)


if __name__ == "__main__":
    unittest.main()
