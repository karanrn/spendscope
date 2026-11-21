"""Command line: python -m spendscope {init,demo,doctor,sync,serve,report,labels,budgets,
template,backup} [options]. Installed with pip/pipx, the same commands run as `spendscope`."""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from spendscope import SpendScopeError, __version__, budgets, db, labels
from spendscope.config import (ENV_VAR, currency_symbol, database_path, display_locale, load_config,
                                    rules)

CMD = "python -m spendscope"

DESCRIPTION = f"""\
Import yearly expense sheets and bank statements into SQLite, and explore them in a local dashboard.

New here?
    {CMD} demo      try it on made-up data, no setup needed
    {CMD} init      write a config.toml for your own data
    {CMD} doctor    check the config, credentials and source

The config is --config, else ${ENV_VAR}, else ./config.toml, else the per-user one that
`init --global` writes.
"""

SYNC_HELP = f"""\
Import expenses from your sheet (Google Sheets, or a local .xlsx/.csv) into SQLite.

Safe to run as often as you like: rows already in the database are skipped,
so history builds up over time without duplicates.

    {CMD} sync                 # incremental: from the latest date already stored
    {CMD} sync --full-rescan   # scan all history, to pick up late entries with older dates
    {CMD} sync --mirror        # also apply edits/deletions made in the source
    {CMD} sync --dry-run       # show what would change, write nothing
    {CMD} sync --config other.toml

Bank statements (.xls, .xlsx, .txt, .csv from any bank) are imported instead of the
configured source with --statement. Withdrawals are stored without a Label; ones already
in the database, or already entered in the sheet (same date and amount), are skipped:

    {CMD} sync --statement statements/Acct_Statement_*.xls
    {CMD} sync --statement statement.csv --show-headers   # what was recognised, redacted
"""

SERVE_HELP = f"""\
Run the dashboard locally, with label and budget editing saved to SQLite.

    {CMD} serve                 # opens http://127.0.0.1:8765
    {CMD} serve --no-open
"""

REPORT_HELP = f"""\
Build a standalone, view-only dashboard (one HTML file that works offline) from the SQLite DB.

    {CMD} report                      # writes dashboard.html next to the DB
    {CMD} report --open               # ...and opens it in your browser
    {CMD} report --out ~/Desktop/expenses.html
"""

LABELS_HELP = f"""\
Manage additional labels stored alongside the expense database.

    {CMD} labels add Work --source-label Food --item-contains coffee
    {CMD} labels add Reimbursable --source-label Travel --from 2025-01-01
    {CMD} labels list
    {CMD} labels remove Work --source-label Food --item-contains coffee

Give a Label to expenses that have none (such as bank-statement imports):

    {CMD} labels set-primary Food --item-contains swiggy
    {CMD} labels set-primary Petrol --item-contains "fuel" --from 2026-04-01
    {CMD} labels apply-rules            # the keyword rules under [rules] in config.toml
"""

BUDGETS_HELP = f"""\
Set monthly budgets per Label. The dashboard flags Labels that go over.

    {CMD} budgets set Food 8000                    # every month
    {CMD} budgets set Travel 25000 --month 2026-12  # that month only
    {CMD} budgets list                              # this month's budgets vs spend
    {CMD} budgets list --month 2026-09
    {CMD} budgets remove Travel --month 2026-12

A budget for one month replaces the every-month budget for that month only.
"""

INIT_HELP = f"""\
Write a starter config.toml. With the local file source (the default) it also writes this
year's workbook from the template, so `sync` works straight away.

    {CMD} init                              # asks two questions, writes ./config.toml
    {CMD} init --source file --region us    # no questions
    {CMD} init --global                     # a per-user config, found from any folder

Sources: file (local .xlsx/.csv), gsheet_csv (a public Google Sheet link), gsheet_api
(private yearly Google Sheets; needs a Google Cloud OAuth client).
Regions: in (₹, en-IN), us ($, en-US, month-first dates), uk (£, en-GB), eu (€, de-DE, 1.234,56).
"""

DEMO_HELP = f"""\
Build a demo project with a year of made-up expenses and a bank statement, import it, and open
the dashboard. Nothing in your own config or database is touched.

    {CMD} demo
    {CMD} demo --dir ~/expense-demo --no-serve
"""

DOCTOR_HELP = f"""\
Check the config, Python packages, credentials, source and database, and say what to fix.
Exits with status 1 if a check fails.

    {CMD} doctor
    {CMD} doctor --offline     # don't download from Google
"""

TEMPLATE_HELP = f"""\
Write the yearly workbook template: a tab per month (Jan26 … Dec26) with Date, Expense, Item
and Label columns, a Label dropdown, monthly totals and a Summary tab.

    {CMD} template                   # Expenses <this year>.xlsx in this folder
    {CMD} template --year 2027
    {CMD} template --year 2027 --out ~/Downloads/

For Google Sheets, upload it to Drive, open it, use File > Save as Google Sheets, and name
it "Expenses 2027".
"""

BACKUP_HELP = f"""\
Copy the database safely, even while sync or serve is running.

    {CMD} backup                          # backups/expenses-<date>-<time>.db next to the DB
    {CMD} backup --out ~/Dropbox/expenses.db
"""


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=None, help="path to config.toml")
    # Nested label actions accept --config too, without overwriting one given before the action
    nested = argparse.ArgumentParser(add_help=False)
    nested.add_argument("--config", default=argparse.SUPPRESS, help="path to config.toml")
    raw = argparse.RawDescriptionHelpFormatter

    parser = argparse.ArgumentParser(prog="spendscope", description=DESCRIPTION, formatter_class=raw)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    p = commands.add_parser("init", description=INIT_HELP, formatter_class=raw,
                            help="write a starter config.toml")
    p.add_argument("--config", help="where to write it (default: ./config.toml)")
    p.add_argument("--source", choices=["file", "gsheet_csv", "gsheet_api"])
    p.add_argument("--region", choices=["in", "us", "uk", "eu"], help="currency, number and date format")
    p.add_argument("--global", dest="global_config", action="store_true",
                   help="write the per-user config instead, which is found from any folder")
    p.add_argument("--force", action="store_true", help="replace an existing config.toml")
    p.set_defaults(run=run_init)

    p = commands.add_parser("demo", description=DEMO_HELP, formatter_class=raw,
                            help="try the dashboard on made-up data")
    p.add_argument("--dir", help="folder for the demo project (default: a folder in the temp directory)")
    p.add_argument("--port", type=int, default=8766, help="dashboard port (default: 8766)")
    p.add_argument("--no-serve", action="store_true", help="build and import only")
    p.add_argument("--no-open", action="store_true", help="do not open the browser automatically")
    p.set_defaults(run=run_demo)

    p = commands.add_parser("doctor", parents=[common], description=DOCTOR_HELP, formatter_class=raw,
                            help="check the setup and say what to fix")
    p.add_argument("--offline", action="store_true", help="skip downloading from Google")
    p.set_defaults(run=run_doctor)

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
    p.add_argument("--show-headers", action="store_true",
                   help="with --statement: show the columns recognised in each file, with transaction "
                        "data redacted, and import nothing")
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
                            help="bulk-manage additional labels and missing Labels")
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
    a = actions.add_parser("apply-rules", parents=[nested],
                           help="label expenses that have none, using [rules] in config.toml")
    a.add_argument("--dry-run", action="store_true", help="show what would change, write nothing")
    p.set_defaults(run=run_labels)

    p = commands.add_parser("budgets", parents=[common], description=BUDGETS_HELP, formatter_class=raw,
                            help="set monthly budgets per Label")
    actions = p.add_subparsers(dest="action", required=True)
    a = actions.add_parser("list", parents=[nested], help="show budgets against spending for a month")
    a.add_argument("--month", help="YYYY-MM (default: the current month)")
    a = actions.add_parser("set", parents=[nested], help="set a Label's monthly budget")
    a.add_argument("label", help="Label to budget")
    a.add_argument("amount", help="budget amount for a month")
    a.add_argument("--month", default="", help="YYYY-MM to budget only that month (default: every month)")
    a = actions.add_parser("remove", parents=[nested], help="remove a Label's budget")
    a.add_argument("label", help="Label whose budget to remove")
    a.add_argument("--month", default="", help="YYYY-MM to remove only that month's budget")
    p.set_defaults(run=run_budgets)

    p = commands.add_parser("template", description=TEMPLATE_HELP, formatter_class=raw,
                            help="write the yearly workbook template")
    p.add_argument("--year", type=int, help="year of the workbook (default: this year)")
    p.add_argument("--out", help="file or folder to write to (default: this folder)")
    p.add_argument("--force", action="store_true", help="overwrite an existing file")
    p.set_defaults(run=run_template)

    p = commands.add_parser("backup", parents=[common], description=BACKUP_HELP, formatter_class=raw,
                            help="copy the database safely")
    p.add_argument("--out", help="backup file path")
    p.set_defaults(run=run_backup)
    return parser


def run_init(args: argparse.Namespace) -> None:
    from spendscope.scaffold import init_project
    init_project(Path(args.config) if args.config else None, args.source, args.region,
                 args.global_config, args.force)


def run_demo(args: argparse.Namespace) -> None:
    from spendscope.demo import run_demo as demo
    demo(Path(args.dir).expanduser().resolve() if args.dir else None, not args.no_serve,
         not args.no_open, args.port)


def run_doctor(args: argparse.Namespace) -> None:
    from spendscope.doctor import run_doctor as doctor
    if not doctor(args.config, args.offline):
        sys.exit(1)


def run_sync(args: argparse.Namespace) -> None:
    from spendscope.sync import sync, sync_statements
    cfg = load_config(args.config)
    if args.show_headers:
        if not args.statement:
            raise SpendScopeError("--show-headers needs --statement FILE…")
        from spendscope.statements import describe_statement
        for p in args.statement:
            print(describe_statement(Path(p).expanduser(), cfg))
        return
    if args.statement:
        if args.mirror or args.full_rescan:
            raise SpendScopeError("--mirror and --full-rescan apply to the sheet source, not --statement")
        sync_statements(cfg, [Path(p).expanduser() for p in args.statement], args.dry_run)
    else:
        sync(cfg, args.mirror, args.dry_run, args.full_rescan)


def run_serve(args: argparse.Namespace) -> None:
    from spendscope.server import serve
    cfg = load_config(args.config)
    serve(database_path(cfg), currency_symbol(cfg), args.host, args.port, not args.no_open,
          display_locale(cfg))


def run_report(args: argparse.Namespace) -> None:
    from spendscope.report import write_report
    cfg = load_config(args.config)
    db_path = database_path(cfg)
    out = Path(args.out).expanduser() if args.out else db_path.parent / "dashboard.html"
    count = write_report(db_path, currency_symbol(cfg), out, display_locale(cfg))
    print(f"Dashboard written: {out}  ({count} expenses)")
    if args.open:
        from spendscope.server import open_in_browser
        open_in_browser(out.resolve().as_uri())


def run_labels(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    con = db.connect(database_path(cfg))
    try:
        if args.action == "list":
            counts = labels.label_counts(con)
            for label, count in counts:
                print(f"{label}: {count} expenses")
            if not counts:
                print("No additional labels yet.")
            print(f"{labels.unlabeled_count(con)} expenses have no Label (set one with set-primary).")
            return
        if args.action == "apply-rules":
            configured = rules(cfg)
            if not configured:
                print("No [rules] in config.toml. Add some, such as:  Food = [\"swiggy\", \"zomato\"]")
                return
            counts = labels.apply_rules(con, configured, args.dry_run)
            verb = "Would set" if args.dry_run else "Set"
            for label, n in counts.items():
                print(f"{verb} Label {label!r} on {n} expenses.")
            print(f"{sum(counts.values())} expenses matched a rule; "
                  f"{labels.unlabeled_count(con) - (sum(counts.values()) if args.dry_run else 0)} still have no Label.")
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


def run_budgets(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    symbol = currency_symbol(cfg)
    con = db.connect(database_path(cfg))
    try:
        when = f"for {args.month}" if args.month else "for every month"
        if args.action == "set":
            label = budgets.set_budget(con, args.label, args.amount, args.month)
            print(f"Budget for {label!r} {when}: {symbol}{budgets.clean_amount(args.amount):,.0f}")
            return
        if args.action == "remove":
            removed = budgets.remove_budget(con, args.label, args.month)
            print(f"Removed the budget for {args.label!r} {when}." if removed
                  else f"No budget for {args.label!r} {when}.")
            return
        month = budgets.clean_month(args.month) or date.today().strftime("%Y-%m")
        rows = budgets.month_status(con, month)
        if not rows:
            print(f"No budgets in effect for {month}.")
            return
        print(f"Budgets for {month}:")
        for label, amount, spent, only in sorted(rows, key=lambda r: -r[2] / r[1]):
            diff = spent - amount
            state = f"{symbol}{diff:,.0f} OVER" if diff > 0 else f"{symbol}{-diff:,.0f} left"
            print(f"  {label}: spent {symbol}{spent:,.0f} of {symbol}{amount:,.0f}"
                  f" ({diff / amount * 100:+.0f}%, {state}){' [this month only]' if only else ''}")
    finally:
        con.close()


def run_template(args: argparse.Namespace) -> None:
    from spendscope.template import workbook_name, write_template
    year = args.year or date.today().year
    out = Path(args.out).expanduser() if args.out else Path.cwd()
    if out.is_dir() or (args.out and args.out.endswith(("/", "\\"))):
        out = out / workbook_name(year)
    write_template(out, year, args.force)
    print(f"Wrote {out}")


def run_backup(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    out = db.backup(database_path(cfg), Path(args.out).expanduser() if args.out else None)
    print(f"Backed up {database_path(cfg)} to {out}")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        args.run(args)
    except SpendScopeError as exc:
        sys.exit(str(exc))
    except KeyboardInterrupt:
        sys.exit(130)
