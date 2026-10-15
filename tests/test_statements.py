import sqlite3
import unittest
from contextlib import closing

from expense_tracker import ExpenseTrackerError, db, labels
from expense_tracker.cli import main
from expense_tracker.statements import read_statement, read_statements
from expense_tracker.sync import sync, sync_statements
from tests.helpers import CSV_ROWS, Project, quietly, write_statements


class StatementParsingTests(unittest.TestCase):
    def setUp(self):
        self.project = Project()
        self.txt, self.xlsx = write_statements(self.project.root)

    def tearDown(self):
        self.project.cleanup()

    def test_withdrawals_are_unlabeled_expenses_and_deposits_skipped(self):
        records, deposits = read_statement(self.txt)
        self.assertEqual(len(deposits), 1)
        self.assertEqual([(r["date"], r["amount"], r["payment"]) for r in records],
                         [("2026-01-01", 449.6, "UPI"), ("2026-01-02", 1500.0, "Card"),
                          ("2026-01-03", 5000.0, "ACH")])
        self.assertEqual({r["category"] for r in records}, {""})
        self.assertEqual(records[0]["description"], "UPI-CAFE COFFEE-PAYMENT FROM PHONE")
        self.assertIn('"Ref": "0000000000000111"', records[0]["extra"])

    def test_txt_and_spreadsheet_exports_are_the_same_transactions(self):
        from_txt, _ = read_statements([self.txt])
        from_xlsx, _ = read_statements([self.xlsx])
        self.assertEqual([r["row_key"] for r in from_txt], [r["row_key"] for r in from_xlsx])
        both, deposits = read_statements([self.txt, self.xlsx])
        self.assertEqual((len(both), deposits), (3, 1))

    def test_file_without_transaction_header_is_a_user_error(self):
        bad = self.project.root / "notes.txt"
        bad.write_text("just,some,text\n", encoding="utf-8")
        with self.assertRaises(ExpenseTrackerError):
            read_statement(bad)


class StatementSyncTests(unittest.TestCase):
    def setUp(self):
        self.project = Project()
        self.cfg = self.project.config()
        self.db_path = self.project.root / "data" / "expenses.db"
        self.txt, self.xlsx = write_statements(self.project.root)

    def tearDown(self):
        self.project.cleanup()

    def rows(self):
        with closing(sqlite3.connect(self.db_path)) as con:
            return con.execute("SELECT origin, date, amount, category FROM expenses ORDER BY date, id").fetchall()

    def test_import_is_unlabeled_and_repeat_import_adds_nothing(self):
        quietly(sync_statements, self.cfg, [self.txt])
        self.assertEqual(self.rows(), [("bank", "2026-01-01", 449.6, ""), ("bank", "2026-01-02", 1500.0, ""),
                                       ("bank", "2026-01-03", 5000.0, "")])
        _, out = quietly(sync_statements, self.cfg, [self.xlsx])
        self.assertIn("new: 0   already stored: 3", out)
        self.assertEqual(len(self.rows()), 3)

    def test_dry_run_writes_no_expenses(self):
        _, out = quietly(sync_statements, self.cfg, [self.txt], dry_run=True)
        self.assertIn("Dry run: nothing written.", out)
        self.assertEqual(self.rows(), [])

    def test_withdrawals_already_in_the_sheet_are_skipped(self):
        quietly(sync, self.cfg)
        _, out = quietly(sync_statements, self.cfg, [self.txt])
        self.assertIn("new: 1   already stored: 0   already in sheet: 2", out)
        self.assertEqual([r for r in self.rows() if r[0] == "bank"], [("bank", "2026-01-03", 5000.0, "")])

    def test_sheet_rows_replace_bank_rows_imported_first_and_keep_their_labels(self):
        quietly(sync_statements, self.cfg, [self.txt])
        with closing(db.connect(self.db_path)) as con:
            coffee = con.execute("SELECT id FROM expenses WHERE amount = 449.6").fetchone()[0]
            labels.add_label(con, [coffee], "Work")
        _, out = quietly(sync, self.cfg)  # bank dates are later, but the sheet still imports
        self.assertIn("replacing bank-statement rows: 2", out)
        self.assertEqual(self.rows(), [("sheet", "2026-01-01", 450.0, "Food"),
                                       ("sheet", "2026-01-02", 1500.0, "Grocery"),
                                       ("sheet", "2026-01-02", 1500.0, "Grocery"),
                                       ("bank", "2026-01-03", 5000.0, "")])
        with closing(db.connect(self.db_path)) as con:
            self.assertEqual(con.execute(
                "SELECT e.description FROM expense_labels l JOIN expenses e ON e.id = l.expense_id"
            ).fetchall(), [("Coffee and sandwich",)])
        _, out = quietly(sync_statements, self.cfg, [self.txt])
        self.assertIn("new: 0", out)
        self.assertEqual(len(self.rows()), 4)

    def test_sheet_mirror_leaves_bank_rows_alone(self):
        quietly(sync, self.cfg)
        quietly(sync_statements, self.cfg, [self.txt])
        self.project.write_csv(CSV_ROWS[:3])
        quietly(sync, self.cfg, mirror=True)
        self.assertIn(("bank", "2026-01-03", 5000.0, ""), self.rows())

    def test_cli_rejects_sheet_only_options_with_statement(self):
        with self.assertRaises(SystemExit):
            quietly(main, ["sync", "--config", str(self.project.config_path),
                           "--statement", str(self.txt), "--mirror"])


class PrimaryLabelTests(unittest.TestCase):
    def setUp(self):
        self.project = Project()
        self.cfg = self.project.config()
        self.txt, _ = write_statements(self.project.root)
        quietly(sync, self.cfg)
        quietly(sync_statements, self.cfg, [self.txt])
        self.con = db.connect(self.project.root / "data" / "expenses.db")

    def tearDown(self):
        self.con.close()
        self.project.cleanup()

    def test_only_expenses_without_a_label_change(self):
        unlabeled = labels.find_unlabeled(self.con, item_contains="groww")
        self.assertEqual(len(unlabeled), 1)
        labelled = labels.find_expenses(self.con, "Food")
        self.assertEqual(labels.set_primary_label(self.con, labelled + unlabeled, "investment"), 1)
        self.assertEqual(labels.primary_label(self.con, unlabeled[0]), "investment")
        self.assertEqual(labels.primary_label(self.con, labelled[0]), "Food")
        self.assertEqual(labels.unlabeled_count(self.con), 0)

    def test_existing_label_keeps_its_spelling(self):
        unlabeled = labels.find_unlabeled(self.con)
        labels.set_primary_label(self.con, unlabeled, "food")
        self.assertEqual(labels.primary_label(self.con, unlabeled[0]), "Food")

    def test_cli_set_primary_and_list(self):
        config = str(self.project.config_path)
        _, out = quietly(main, ["labels", "list", "--config", config])
        self.assertIn("1 expenses have no Label", out)
        _, out = quietly(main, ["labels", "set-primary", "Investment", "--item-contains", "GROWW",
                                "--config", config])
        self.assertIn("Set Label 'Investment' on 1 expenses", out)


if __name__ == "__main__":
    unittest.main()
