import sqlite3
import unittest
from contextlib import closing

from spendscope import SpendScopeError, db
from spendscope.config import load_config
from spendscope.sync import sync
from tests.helpers import CSV_ROWS, Project, quietly


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.project = Project()
        self.cfg = self.project.config()
        self.db_path = self.project.root / "data" / "expenses.db"

    def tearDown(self):
        self.project.cleanup()

    def rows(self):
        with closing(sqlite3.connect(self.db_path)) as con:
            return con.execute("SELECT date, amount, description FROM expenses ORDER BY id").fetchall()

    def test_config_paths_are_relative_to_config_file(self):
        self.assertEqual(self.cfg["database"]["path"], str(self.db_path))
        self.assertEqual(self.cfg["source"]["file_path"], str(self.project.root / "expenses.csv"))

    def test_missing_config_is_a_user_error(self):
        with self.assertRaises(SpendScopeError):
            load_config(self.project.root / "nope.toml")

    def test_first_sync_imports_and_repeat_sync_adds_nothing(self):
        quietly(sync, self.cfg)
        self.assertEqual(self.rows(), [("2026-01-01", 450.0, "Coffee and sandwich"),
                                       ("2026-01-02", 1500.0, "Groceries"),
                                       ("2026-01-02", 1500.0, "Groceries")])
        _, out = quietly(sync, self.cfg)
        self.assertIn("new: 0", out)
        self.assertEqual(len(self.rows()), 3)

    def test_dry_run_writes_no_expenses(self):
        _, out = quietly(sync, self.cfg, dry_run=True)
        self.assertIn("Dry run: nothing written.", out)
        self.assertEqual(self.rows(), [])

    def test_incremental_sync_ignores_older_late_entries_until_full_rescan(self):
        quietly(sync, self.cfg)
        self.project.write_csv(CSV_ROWS + ["01/01/2026,99,Late entry,Food,,,"])
        quietly(sync, self.cfg)
        self.assertNotIn(("2026-01-01", 99.0, "Late entry"), self.rows())
        quietly(sync, self.cfg, full_rescan=True)
        self.assertIn(("2026-01-01", 99.0, "Late entry"), self.rows())

    def test_mirror_removes_rows_deleted_from_source(self):
        quietly(sync, self.cfg)
        self.project.write_csv(CSV_ROWS[:3])  # drop the duplicate Groceries row
        quietly(sync, self.cfg, mirror=True)
        self.assertEqual(len(self.rows()), 2)

    def test_unreadable_rows_go_to_failures_log(self):
        self.project.write_csv(CSV_ROWS + ["03/01/2026,lots,Mystery,Misc,,,"])
        _, out = quietly(sync, self.cfg)
        self.assertIn("1 rows could not be parsed", out)
        log = (self.project.root / "parse_failures.csv").read_text(encoding="utf-8")
        self.assertIn("unparseable amount", log)


class DatabaseTests(unittest.TestCase):
    def test_old_database_gains_year_and_origin_columns(self):
        project = Project()
        self.addCleanup(project.cleanup)
        path = project.root / "old.db"
        with closing(sqlite3.connect(path)) as con, con:
            con.execute("CREATE TABLE expenses (id INTEGER PRIMARY KEY, row_key TEXT, date TEXT,"
                        " month TEXT, amount REAL, category TEXT, subcategory TEXT, description TEXT,"
                        " payment TEXT, extra TEXT, first_seen TEXT, sync_id INTEGER)")
            con.execute("INSERT INTO expenses(row_key, date, month, amount, category, first_seen)"
                        " VALUES ('k', '2025-03-04', '2025-03', 10, 'Food', 'now')")
        con = db.connect(path)
        self.addCleanup(con.close)
        self.assertEqual(con.execute("SELECT year, origin FROM expenses").fetchone(), (2025, "sheet"))

    def test_connect_requires_existing_database_unless_creating(self):
        project = Project()
        self.addCleanup(project.cleanup)
        with self.assertRaises(SpendScopeError):
            db.connect(project.root / "missing.db")


if __name__ == "__main__":
    unittest.main()
