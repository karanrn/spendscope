"""Command line: python -m expense_tracker {sync,serve,report,labels} [options]."""
from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

from expense_tracker import ExpenseTrackerError, db, labels
from expense_tracker.config import DEFAULT_CONFIG, currency_symbol, database_path, load_config

SYNC_HELP = """\
Sync expenses from a Google Sheet (or a local .xlsx/.csv) into SQLite.

Safe to run as often as you like: rows already in the database are skipped,
so history builds up over time without duplicates.

    python sync.py                 # sync using config.toml
    python sync.py --full-rescan   # scan all history (default starts at latest DB date)
    python sync.py --mirror        # also apply edits/deletions made in the source
    python sync.py --dry-run       # show what would change, write nothing
    python sync.py --config other.toml

Bank statements (HDFC-style .xls or comma-delimited .txt) are imported instead of
the configured source with --statement. Withdrawals are stored without a Label;
ones already in the database, or already entered in the sheet (same date and
amount), are skipped:

    python sync.py --statement Acct_Statement_*.xls
"""

SERVE_HELP = """\
Run the dashboard locally with SQLite-backed custom-label editing.

    python serve.py                 # opens http://127.0.0.1:8765
    python serve.py --no-open
"""

REPORT_HELP = """\
Build a standalone, view-only dashboard (a single HTML file) from the SQLite DB.

    python report.py                      # writes dashboard.html next to the DB
    python report.py --open               # ...and opens it in your browser
    python report.py --out ~/Desktop/expenses.html
"""

LABELS_HELP = """\
Manage additional labels stored alongside the expense database.

    python labels.py add Work --source-label Food --item-contains coffee
    python labels.py add Reimbursable --source-label Travel --from 2025-01-01
    python labels.py list
    python labels.py remove Work --source-label Food --item-contains coffee

Give a Label to expenses that have none (such as bank-statement imports):

    python labels.py set-primary Food --item-contains swiggy
    python labels.py set-primary Petrol --item-contains "fuel" --from 2026-04-01
"""


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=DEFAULT_CONFIG, help="path to config.toml")
    # Nested label actions accept --config too, without overwriting one given before the action
    nested = argparse.ArgumentParser(add_help=False)
    nested.add_argument("--config", default=argparse.SUPPRESS, help="path to config.toml")
    raw = argparse.RawDescriptionHelpFormatter

    parser = argparse.ArgumentParser(prog="expense_tracker", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    p = commands.add_parser("sync", parents=[common], description=SYNC_HELP, formatter_class=raw,
                            help="import expenses into SQLite")
    p.add_argument("--mirror", action="store_true",
                   help="delete DB rows (within the sheet's date range) that are no longer in the sheet, "
                        "so edits and deletions carry over")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--full-rescan", action="store_true",
                   help="scan all source dates instead of starting at the latest date in the database")
    p.add_argument("--statement", nargs="+", metavar="FILE",
                   help="import these bank statements (.xls, .xlsx, .txt, .csv) instead of the configured source")
    p.set_defaults(run=run_sync)

    p = commands.add_parser("serve", parents=[common], description=SERVE_HELP, formatter_class=raw,
                            help="run the editable local dashboard")
    p.add_argument("--host", default="127.0.0.1", help="bind address (default: localhost only)")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-open", action="store_true", help="do not open the browser automatically")
    p.set_defaults(run=run_serve)

    p = commands.add_parser("report", parents=[common], description=REPORT_HELP, formatter_class=raw,
                            help="write a static dashboard.html")
    p.add_argument("--out", help="output HTML path (default: dashboard.html next to the DB)")
    p.add_argument("--open", action="store_true", help="open the dashboard in a browser")
    p.set_defaults(run=run_report)

    p = commands.add_parser("labels", parents=[common], description=LABELS_HELP, formatter_class=raw,
                            help="bulk-manage additional labels")
    actions = p.add_subparsers(dest="action", required=True)
    actions.add_parser("list", parents=[nested],
                       help="show extra labels and how many expenses use them")
    for name in ("add", "remove"):
        a = actions.add_parser(name, parents=[nested])
        a.add_argument("label", help="additional label to add or remove")
        a.add_argument("--source-label", required=True, help="existing Label/category to match")
        a.add_argument("--item-contains", help="case-insensitive text contained in Item")
        a.add_argument("--from", dest="from_date", help="inclusive ISO date (YYYY-MM-DD)")
        a.add_argument("--to", dest="to_date", help="inclusive ISO date (YYYY-MM-DD)")
    a = actions.add_parser("set-primary", parents=[nested],
                           help="set the Label of expenses that have none")
    a.add_argument("label", help="Label to set")
    a.add_argument("--item-contains", help="case-insensitive text contained in Item")
    a.add_argument("--from", dest="from_date", help="inclusive ISO date (YYYY-MM-DD)")
    a.add_argument("--to", dest="to_date", help="inclusive ISO date (YYYY-MM-DD)")
    p.set_defaults(run=run_labels)
    return parser


def run_sync(args: argparse.Namespace) -> None:
    from expense_tracker.sync import sync, sync_statements
    cfg = load_config(args.config)
    if args.statement:
        if args.mirror or args.full_rescan:
            raise ExpenseTrackerError("--mirror and --full-rescan apply to the sheet source, not --statement")
        sync_statements(cfg, [Path(p).expanduser() for p in args.statement], args.dry_run)
    else:
        sync(cfg, args.mirror, args.dry_run, args.full_rescan)


def run_serve(args: argparse.Namespace) -> None:
    from expense_tracker.server import serve
    cfg = load_config(args.config)
    serve(database_path(cfg), currency_symbol(cfg), args.host, args.port, not args.no_open)


def run_report(args: argparse.Namespace) -> None:
    from expense_tracker.report import write_report
    cfg = load_config(args.config)
    db_path = database_path(cfg)
    out = Path(args.out).expanduser() if args.out else db_path.parent / "dashboard.html"
    count = write_report(db_path, currency_symbol(cfg), out)
    print(f"Dashboard written: {out}  ({count} expenses)")
    if args.open:
        webbrowser.open_new_tab(out.resolve().as_uri())  # reuse the open browser window


def run_labels(args: argparse.Namespace) -> None:
    con = db.connect(database_path(load_config(args.config)))
    try:
        if args.action == "list":
            counts = labels.label_counts(con)
            for label, count in counts:
                print(f"{label}: {count} expenses")
            if not counts:
                print("No additional labels yet.")
            print(f"{labels.unlabeled_count(con)} expenses have no Label (set one with set-primary).")
            return
        label = labels.clean_label(args.label)
        if args.action == "set-primary":
            ids = labels.find_unlabeled(con, args.item_contains, args.from_date, args.to_date)
            changed = labels.set_primary_label(con, ids, label)
            print(f"Set Label {label!r} on {changed} expenses that had none.")
            return
        ids = labels.find_expenses(con, args.source_label, args.item_contains,
                                   args.from_date, args.to_date)
        if args.action == "add":
            changed = labels.add_label(con, ids, label)
            print(f"Added {label!r} on {changed} of {len(ids)} matching expenses.")
        else:
            changed = labels.remove_label(con, ids, label)
            print(f"Removed {label!r} from {changed} of {len(ids)} matching expenses.")
    finally:
        con.close()


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        args.run(args)
    except ExpenseTrackerError as exc:
        sys.exit(str(exc))
