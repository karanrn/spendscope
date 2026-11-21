"""Locale-aware amounts, statement deposits as income, --show-headers, [rules], file globs,
and the self-contained dashboard."""
import json
import re
import sqlite3
import unittest
from contextlib import closing

from spendscope import SpendScopeError, db, labels
from spendscope.cli import main
from spendscope.config import database_path
from spendscope.parsing import parse_amount
from spendscope.report import load, render_dashboard
from spendscope.statements import describe_statement, read_statement, redact
from spendscope.sync import sync, sync_statements
from tests.helpers import CSV_ROWS, Project, add_config, quietly, write_statements


class AmountTests(unittest.TestCase):
    def test_decimal_comma_and_currency_codes(self):
        self.assertEqual(parse_amount("1.234,56", "€", ","), 1234.56)
        self.assertEqual(parse_amount("€ 1 234,5", "€", ","), 1234.5)
        self.assertEqual(parse_amount("12,5", "€", ","), 12.5)
        self.assertEqual(parse_amount("$1,234.56", "$"), 1234.56)
        self.assertEqual(parse_amount("USD 99", "$"), 99.0)
        self.assertEqual(parse_amount("CHF 1'200", "Fr", code="CHF"), 1200.0)
        self.assertIsNone(parse_amount("n/a", "€", ","))


class StatementFeatureTests(unittest.TestCase):
    def setUp(self):
        self.project = Project()
        self.txt, self.xlsx = write_statements(self.project.root)

    def tearDown(self):
        self.project.cleanup()

    def table(self, sql):
        with closing(sqlite3.connect(database_path(self.project.config()))) as con:
            return con.execute(sql).fetchall()

    def test_deposits_are_skipped_unless_import_deposits(self):
        cfg = self.project.config()
        _, out = quietly(sync_statements, cfg, [self.txt])
        self.assertIn("1 deposits skipped", out)
        self.assertEqual(self.table("SELECT COUNT(*) FROM income"), [(0,)])

        add_config(self.project, "[statements]\nimport_deposits = true")
        cfg = self.project.config()
        _, out = quietly(sync_statements, cfg, [self.txt, self.xlsx], dry_run=True)
        self.assertIn("new income: 1", out)
        self.assertEqual(self.table("SELECT COUNT(*) FROM income"), [(0,)])
        quietly(sync_statements, cfg, [self.txt, self.xlsx])  # the same deposit in both files
        quietly(sync_statements, cfg, [self.txt])
        self.assertEqual(self.table("SELECT date, amount, description, payment FROM income"),
                         [("2026-01-01", 200.0, "UPI-A FRIEND-SPLIT", "UPI")])
        self.assertEqual(load(database_path(cfg))["income"], [["2026-01-01", 200.0, "UPI-A FRIEND-SPLIT"]])

    def test_decimal_comma_statement(self):
        path = self.project.root / "eu.csv"
        path.write_text("Buchungstag;Verwendungszweck;Betrag;Saldo\n"
                        "02.01.2026;REWE MARKT;-1.234,56;8.765,44\n"
                        "03.01.2026;GEHALT;2.500,00;11.265,44\n", encoding="utf-8")
        cfg = {"parsing": {"decimal_separator": ","},
               "statements": {"columns": {"date": "Buchungstag", "narration": "Verwendungszweck",
                                          "amount": "Betrag", "balance": "Saldo"}}}
        records, deposits = read_statement(path, cfg)
        self.assertEqual([(r["amount"], r["description"]) for r in records], [(1234.56, "REWE MARKT")])
        self.assertEqual([d["amount"] for d in deposits], [2500.0])

    def test_show_headers_redacts_transaction_data(self):
        text = describe_statement(self.txt)
        self.assertIn("header found on row 2", text)
        self.assertIn("debit      <- 'Debit Amount'", text)
        sample = text.split("first transaction (redacted): ")[1]
        self.assertNotRegex(sample, r"[A-WYZa-z0-8]")  # only X and 9 masks and punctuation remain
        self.assertEqual(redact("UPI-ACME 1,200.50"), "XXX-XXXX 9,999.99")
        _, out = quietly(main, ["sync", "--config", str(self.project.config_path),
                                "--statement", str(self.xlsx), "--show-headers"])
        self.assertIn("Withdrawal Amt.", out)
        self.assertFalse(database_path(self.project.config()).exists())  # nothing imported

    def test_show_headers_reports_the_closest_row_when_none_fits(self):
        bad = self.project.root / "bad.csv"
        bad.write_text("When,What\n01/01/2026,Coffee\n", encoding="utf-8")
        self.assertIn("couldn't find the transaction header", describe_statement(bad))


class RuleTests(unittest.TestCase):
    def setUp(self):
        self.project = Project()
        self.txt, _ = write_statements(self.project.root)

    def tearDown(self):
        self.project.cleanup()

    def test_rules_label_unlabeled_rows_after_import_first_match_wins(self):
        add_config(self.project, '[rules]\nInvestment = ["groww"]\n"Pay Out" = ["growW", "ach"]')
        cfg = self.project.config()
        quietly(sync, cfg)
        _, out = quietly(sync_statements, cfg, [self.txt], dry_run=True)
        self.assertIn("Rules would set a Label on 1 expenses (Investment 1)", out)
        _, out = quietly(sync_statements, cfg, [self.txt])
        self.assertIn("Rules set a Label on 1 expenses (Investment 1)", out)
        con = db.connect(database_path(cfg))
        try:
            self.assertEqual(con.execute("SELECT category FROM expenses WHERE description LIKE 'ACH%'").fetchall(),
                             [("Investment",)])
            self.assertEqual(labels.apply_rules(con, {"Food": ["groceries"]}), {})  # labelled rows untouched
        finally:
            con.close()

    def test_rules_for_blank_sheet_labels_and_cli(self):
        self.project.write_csv(CSV_ROWS[:1] + ["05/01/2026,90,Swiggy order,,,,"])
        quietly(sync, self.project.config())
        add_config(self.project, '[rules]\nFood = "swiggy"')
        config = str(self.project.config_path)
        _, out = quietly(main, ["labels", "apply-rules", "--dry-run", "--config", config])
        self.assertIn("Would set Label 'Food' on 1 expenses", out)
        _, out = quietly(main, ["labels", "apply-rules", "--config", config])
        self.assertIn("0 still have no Label", out)
        _, out = quietly(sync, self.project.config())  # the sheet row keeps its key: no duplicate
        self.assertIn("new: 0", out)

    def test_bad_rules_are_a_user_error(self):
        add_config(self.project, "[rules]\nFood = 3")
        with self.assertRaises(SpendScopeError):
            quietly(sync, self.project.config())


class FileGlobTests(unittest.TestCase):
    def test_glob_reads_every_matching_file(self):
        project = Project()
        try:
            (project.root / "expenses.csv").rename(project.root / "2025.csv")
            (project.root / "2026.csv").write_text(
                "Date,Expense,Item,Label\n10/02/2026,99,Book,Misc\n", encoding="utf-8")
            config = project.config_path
            config.write_text(config.read_text().replace('"expenses.csv"', '"20*.csv"'), encoding="utf-8")
            _, out = quietly(sync, project.config())
            self.assertIn("new: 4", out)
            config.write_text(config.read_text().replace('"20*.csv"', '"19*.csv"'), encoding="utf-8")
            with self.assertRaises(SpendScopeError):
                sync(project.config())
        finally:
            project.cleanup()


class DashboardTests(unittest.TestCase):
    def test_page_is_self_contained_and_carries_the_locale(self):
        project = Project()
        try:
            quietly(sync, project.config())
            html = render_dashboard(load(database_path(project.config())), "$", locale="en-US")
        finally:
            project.cleanup()
        self.assertNotRegex(html, r"<script[^>]+src=")
        self.assertNotIn("/*__CHARTJS__*/", html)
        self.assertIn("Chart.js v4.4.1", html)
        data = json.loads(re.search(r"const DATA = (.*);\n", html).group(1))
        self.assertEqual((data["locale"], data["currency"], data["income"]), ("en-US", "$", []))


if __name__ == "__main__":
    unittest.main()
