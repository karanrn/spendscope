import sqlite3
import unittest
from contextlib import closing

from spendscope import SpendScopeError, db, labels
from spendscope.cli import main
from spendscope.statements import read_statement, read_statements
from spendscope.sync import sync, sync_statements
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
        self.assertEqual((len(both), len(deposits)), (3, 1))

    def test_file_without_transaction_header_is_a_user_error(self):
        bad = self.project.root / "notes.txt"
        bad.write_text("just,some,text\n", encoding="utf-8")
        with self.assertRaises(SpendScopeError):
            read_statement(bad)


class OtherBankLayoutTests(unittest.TestCase):
    """Made-up statements in layouts typical of other banks (names, dates, amount styles)."""

    def setUp(self):
        self.project = Project()

    def tearDown(self):
        self.project.cleanup()

    def write(self, name, text):
        path = self.project.root / name
        path.write_text(text, encoding="utf-8")
        return path

    def summary(self, path, cfg=None):
        records, deposits = read_statement(path, cfg)
        return [(r["date"], r["amount"], r["description"], r["payment"]) for r in records], len(deposits)

    def test_tab_delimited_txn_date_with_month_names_and_wrapped_narration(self):
        path = self.write("sbi.txt", "\n".join([
            "Account Name\t:\tMr. Someone",
            "Txn Date\tValue Date\tDescription\tRef No./Cheque No.\tDebit\tCredit\tBalance",
            "1 Apr 2026\t1 Apr 2026\tTO TRANSFER-UPI/DR/6081/SHOP NAME\tTRANSFER TO 1234\t1,250.00\t\t98,750.00",
            "\t\tYESB/q123/Payment\t\t\t\t",
            "2 Apr 2026\t2 Apr 2026\tBY TRANSFER-NEFT*SALARY\tTRANSFER FROM 99\t\t50,000.00\t1,48,750.00",
            "",
            "**This is a computer generated statement**",
        ]))
        self.assertEqual(self.summary(path), (
            [("2026-04-01", 1250.0, "TO TRANSFER-UPI/DR/6081/SHOP NAME YESB/q123/Payment", "UPI")], 1))

    def test_single_amount_column_with_dr_cr_column_and_times(self):
        path = self.write("kotak.csv", "\n".join([
            "Sl. No.,Transaction Date,Value Date,Description,Chq / Ref No.,Amount,Dr / Cr,Balance,Dr / Cr",
            "1,01-04-2026 10:15:02,01-04-2026,POS 4321XXXX MOVIE HALL,REF1,\"1,200.00\",DR,\"8,800.00\",CR",
            "2,02-04-2026 09:00:00,02-04-2026,IMPS-REFUND,REF2,300.00,CR,\"9,100.00\",CR",
        ]))
        self.assertEqual(self.summary(path), ([("2026-04-01", 1200.0, "POS 4321XXXX MOVIE HALL", "Card")], 1))

    def test_short_dr_cr_headers(self):
        path = self.write("axis.csv", "\n".join([
            "Tran Date,CHQNO,PARTICULARS,DR,CR,BAL,SOL",
            "05-04-2026,,UPI/P2M/1234/GROCER,450.00,,9550.00,1",
            "06-04-2026,,INT.PD:01-01-2026 to 31-03-2026,,25.00,9575.00,1",
        ]))
        self.assertEqual(self.summary(path), ([("2026-04-05", 450.0, "UPI/P2M/1234/GROCER", "UPI")], 1))

    def test_html_table_saved_as_xls(self):
        path = self.write("icici.xls", """<html><body><table>
            <tr><td>Account Number</td><td>XXXX1234</td></tr>
            <tr><th>S No.</th><th>Value Date</th><th>Transaction Date</th><th>Cheque Number</th>
                <th>Transaction Remarks</th><th>Withdrawal Amount (INR )</th>
                <th>Deposit Amount (INR )</th><th>Balance (INR )</th></tr>
            <tr><td>1</td><td>03/04/2026</td><td>03/04/2026</td><td>-</td>
                <td>ATM/CASH WDL/03-04-26</td><td>2,000.00</td><td>0.00</td><td>7,000.00</td></tr>
            </table></body></html>""")
        self.assertEqual(self.summary(path), ([("2026-04-03", 2000.0, "ATM/CASH WDL/03-04-26", "ATM")], 0))

    def test_signed_amounts_and_dr_suffixes(self):
        path = self.write("signed.csv", "\n".join([
            "Date,Details,Amount",
            "2026-04-07,Electricity bill,-1500.50",
            "2026-04-08,Card payment,250.00 Dr",
            "2026-04-09,Interest,12.00",
        ]))
        self.assertEqual(self.summary(path), ([("2026-04-07", 1500.5, "Electricity bill", "Bank"),
                                               ("2026-04-08", 250.0, "Card payment", "Bank")], 1))

    def test_spreadsheet_date_cells(self):
        from datetime import datetime
        from openpyxl import Workbook
        wb = Workbook()
        wb.active.append(["Date", "Narration", "Withdrawal", "Deposit"])
        wb.active.append([datetime(2026, 4, 10), "NEFT-RENT", 20000, None])
        path = self.project.root / "dates.xlsx"
        wb.save(path)
        self.assertEqual(self.summary(path), ([("2026-04-10", 20000.0, "NEFT-RENT", "NEFT")], 0))

    def test_unrecognised_headers_can_be_named_in_config(self):
        path = self.write("odd.csv", "\n".join([
            "Posted On,What,Out,In",
            "11/04/2026,Bookshop,640,",
        ]))
        with self.assertRaises(SpendScopeError) as err:
            read_statement(path)
        self.assertIn("Closest row", str(err.exception))
        cfg = {"statements": {"columns": {"date": "Posted On", "narration": ["What", "Memo"],
                                          "debit": "Out", "credit": "In"}}}
        self.assertEqual(self.summary(path, cfg), ([("2026-04-11", 640.0, "Bookshop", "Bank")], 0))

    def test_unknown_config_column_is_a_user_error(self):
        path = self.write("x.csv", "Date,Narration,Debit,Credit\n")
        with self.assertRaises(SpendScopeError):
            read_statement(path, {"statements": {"columns": {"payee": "Who"}}})


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
