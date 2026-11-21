"""SpendScope: expense sheets and bank statements → SQLite → dashboard."""

__version__ = "0.2.0"


class SpendScopeError(Exception):
    """A problem the user can fix (bad config, missing database, unreadable source).

    Library code raises this instead of exiting; the CLI prints it and exits.
    """
