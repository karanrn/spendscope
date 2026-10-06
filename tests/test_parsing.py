import unittest
from datetime import date, datetime

from expense_tracker import ExpenseTrackerError
from expense_tracker.parsing import header_key, parse_amount, parse_date, to_records
from expense_tracker.sources import SheetRow, add_year_to_short_dates, gsheet_csv_url, skipped_sheet

CFG = {
    "columns": {"date": "Date", "amount": "Expense", "category": "Label", "description": "Item"},
    "parsing": {"date_formats": ["%d/%m/%Y", "%Y-%m-%d"], "currency_symbol": "₹"},
}


class ParseValueTests(unittest.TestCase):
    def test_amounts(self):
        self.assertEqual(parse_amount(450, "₹"), 450.0)
        self.assertEqual(parse_amount("₹1,250.50", "₹"), 1250.5)
        self.assertEqual(parse_amount("Rs. 99", "₹"), 99.0)
        self.assertEqual(parse_amount("Rs.99", "₹"), 99.0)
        self.assertEqual(parse_amount("INR 1,000", "₹"), 1000.0)
        self.assertEqual(parse_amount("(200)", "₹"), -200.0)
        self.assertIsNone(parse_amount("", "₹"))
        self.assertIsNone(parse_amount("lots", "₹"))
        self.assertIsNone(parse_amount(True, "₹"))

    def test_dates(self):
        formats = ["%d/%m/%Y", "%Y-%m-%d"]
        self.assertEqual(parse_date("05/01/2026", formats), date(2026, 1, 5))
        self.assertEqual(parse_date("2026-01-05 00:00:00", formats), date(2026, 1, 5))
        self.assertEqual(parse_date(datetime(2026, 1, 5, 9), formats), date(2026, 1, 5))
        self.assertEqual(parse_date(46027, formats), date(2026, 1, 5))  # spreadsheet serial
        self.assertIsNone(parse_date("someday", formats))

    def test_header_units_are_ignored(self):
        self.assertEqual(header_key(" Expense (INR) "), "expense")


class ToRecordsTests(unittest.TestCase):
    def records(self, rows):
        return to_records([SheetRow(r, f"sheet:{i}") for i, r in enumerate(rows, 1)], CFG)

    def test_side_panel_does_not_hide_expense_on_same_row(self):
        out, skipped, failures = self.records([
            ["Date", "Expense(INR)", "Item", "Label", "", "Total", "=SUM()"],
            ["01/01/2026", "450", "Coffee", "food", "", "Total", "450"],
            ["", "", "", "", "", "Food", "450"],
        ])
        self.assertEqual([(r["date"], r["amount"], r["category"]) for r in out],
                         [("2026-01-01", 450.0, "Food")])
        self.assertEqual((skipped, failures), (1, []))

    def test_blank_date_continues_previous_date(self):
        out, _, _ = self.records([
            ["Date", "Expense", "Item", "Label"],
            ["01/01/2026", "100", "Tea", "Food"],
            ["", "200", "Snacks", "Food"],
        ])
        self.assertEqual([r["date"] for r in out], ["2026-01-01", "2026-01-01"])

    def test_zero_and_total_rows_are_skipped(self):
        out, skipped, failures = self.records([
            ["Date", "Expense", "Item", "Label"],
            ["01/01/2026", "0", "Free", "Misc"],
            ["", "500", "Total", ""],
        ])
        self.assertEqual((out, skipped, failures), ([], 2, []))

    def test_unreadable_rows_are_reported(self):
        out, _, failures = self.records([
            ["Date", "Expense", "Item", "Label"],
            ["01/01/2026", "abc", "Lunch", "Food"],
            ["31/31/2026", "100", "Dinner", "Food"],
        ])
        self.assertEqual(out, [])
        self.assertEqual([(f["reason"], f["source"]) for f in failures],
                         [("unparseable amount", "sheet:2"), ("unparseable date", "sheet:3")])

    def test_identical_rows_get_distinct_stable_keys(self):
        rows = [["Date", "Expense", "Item", "Label"],
                ["01/01/2026", "50", "Coffee", "Food"],
                ["01/01/2026", "50", "Coffee", "Food"]]
        first, _, _ = self.records(rows)
        again, _, _ = self.records(rows)
        self.assertEqual(len({r["row_key"] for r in first}), 2)
        self.assertEqual([r["row_key"] for r in first], [r["row_key"] for r in again])

    def test_missing_header_is_a_user_error(self):
        with self.assertRaises(ExpenseTrackerError):
            self.records([["When", "How much"]])


class SourceHelperTests(unittest.TestCase):
    def test_short_api_dates_get_workbook_year(self):
        rows = [["Date", "Expense"], ["1/5", "100"], ["2/30", "5"]]
        add_year_to_short_dates(rows, 2026)
        self.assertEqual(rows[1][0], "2026-01-05")
        self.assertEqual(rows[2][0], "2/30")  # not a real date: left for the failures log

    def test_csv_export_url(self):
        self.assertEqual(
            gsheet_csv_url("https://docs.google.com/spreadsheets/d/abc123/edit#gid=7"),
            "https://docs.google.com/spreadsheets/d/abc123/export?format=csv&gid=7")

    def test_summary_tab_is_skipped(self):
        self.assertTrue(skipped_sheet(" summary ", {}))
        self.assertFalse(skipped_sheet("Jan26", {}))


if __name__ == "__main__":
    unittest.main()
