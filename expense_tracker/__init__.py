"""Expense tracker: Google Sheets → SQLite → dashboard."""


class ExpenseTrackerError(Exception):
    """A problem the user can fix (bad config, missing database, unreadable source).

    Library code raises this instead of exiting; the CLI prints it and exits.
    """
