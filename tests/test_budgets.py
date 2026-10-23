import json
import unittest

from expense_tracker import ExpenseTrackerError, budgets, db
from expense_tracker.cli import main
from expense_tracker.config import database_path
from expense_tracker.report import load
from expense_tracker.sync import sync
from tests.helpers import Project, quietly


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.project = Project()
        quietly(sync, self.project.config())
        self.db_path = database_path(self.project.config())
        self.con = db.connect(self.db_path)

    def tearDown(self):
        self.con.close()
        self.project.cleanup()

    def test_set_keeps_existing_label_spelling_and_replaces(self):
        self.assertEqual(budgets.set_budget(self.con, "food", 400), "Food")
        self.assertEqual(budgets.set_budget(self.con, "FOOD", "1,000"), "Food")
        self.assertEqual(budgets.all_budgets(self.con), [("Food", "", 1000.0)])

    def test_month_budget_overrides_every_month_budget(self):
        budgets.set_budget(self.con, "Food", 400)
        budgets.set_budget(self.con, "Grocery", 5000)
        budgets.set_budget(self.con, "Grocery", 2000, "2026-01")
        status = {row[0]: row[1:] for row in budgets.month_status(self.con, "2026-01")}
        self.assertEqual(status, {"Food": (400.0, 450.0, False), "Grocery": (2000.0, 3000.0, True)})
        status = {row[0]: row[1:] for row in budgets.month_status(self.con, "2026-02")}
        self.assertEqual(status["Grocery"], (5000.0, 0.0, False))

    def test_remove_only_the_given_scope(self):
        budgets.set_budget(self.con, "Food", 400)
        budgets.set_budget(self.con, "Food", 900, "2026-01")
        self.assertEqual(budgets.remove_budget(self.con, "food", "2026-01"), 1)
        self.assertEqual(budgets.remove_budget(self.con, "food", "2026-01"), 0)
        self.assertEqual(budgets.all_budgets(self.con), [("Food", "", 400.0)])

    def test_validation(self):
        for amount in (0, -5, "abc", None, True, float("nan"), float("inf")):
            with self.assertRaises(ExpenseTrackerError):
                budgets.set_budget(self.con, "Food", amount)
        for month in ("2026-13", "2026-1", "Jan26"):
            with self.assertRaises(ExpenseTrackerError):
                budgets.set_budget(self.con, "Food", 100, month)
        for label in ("", "Uncategorized", "unlabeled"):
            with self.assertRaises(ExpenseTrackerError):
                budgets.set_budget(self.con, label, 100)

    def test_dashboard_data_includes_budgets(self):
        budgets.set_budget(self.con, "Food", 400)
        self.assertEqual(json.loads(json.dumps(load(self.db_path)["budgets"])), [["Food", "", 400.0]])

    def test_cli_set_list_remove(self):
        config = str(self.project.config_path)
        _, out = quietly(main, ["budgets", "set", "food", "400", "--config", config])
        self.assertIn("Budget for 'Food' for every month: ₹400", out)
        _, out = quietly(main, ["budgets", "list", "--month", "2026-01", "--config", config])
        self.assertIn("Food: spent ₹450 of ₹400 (+12%, 50 OVER)", out)
        _, out = quietly(main, ["budgets", "remove", "Food", "--config", config])
        self.assertIn("Removed the budget for 'Food' for every month.", out)
        _, out = quietly(main, ["budgets", "list", "--month", "2026-01", "--config", config])
        self.assertIn("No budgets in effect for 2026-01.", out)


if __name__ == "__main__":
    unittest.main()
