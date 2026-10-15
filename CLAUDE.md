# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A personal tool that imports yearly Google Sheets (titled `Expenses YYYY`, one tab per month) into SQLite, then shows the data in a local dashboard. Python 3.11+, standard library plus `gspread`, `openpyxl` and `xlrd` (legacy `.xls` bank statements). Bank account statements can also be imported (`sync --statement FILE…`). `README.md` covers user setup, the Sheet template layout, and example SQL.

## Commands

```bash
make test                       # python -m unittest discover -s tests -t .
python -m unittest tests.test_sync                                   # one module
python -m unittest tests.test_sync.SyncTests.test_dry_run_writes_no_expenses  # one test

make dry-run    # sync --dry-run: parse the source and preview inserts without writing
make statement FILES='Acct_*.xls'   # sync --statement: import bank statements
make sync       # import into SQLite
make serve      # editable dashboard at http://127.0.0.1:8765
make report     # static, view-only dashboard.html
make run PYTHON=.venv/bin/python   # override the interpreter (a .venv exists locally)
```

All entry points go through `python -m expense_tracker {sync,serve,report,labels}` (`expense_tracker/cli.py`). The top-level `sync.py`, `serve.py`, `report.py` and `labels.py` are thin shims kept so existing commands and cron jobs keep working. Don't put logic in them.

There is no linter or formatter configured. Tests use only `unittest` and need no network or Google credentials. They build a temp project with a CSV source and config (`tests/helpers.py`).

## Architecture

Pipeline: `sources.read_rows` → `parsing.to_records` → `sync.sync` → SQLite (`db.py`) → `report.load` → `templates/dashboard.html`. Bank statements take a parallel path: `statements.read_statements` → `sync.sync_statements`.

- **config.py**: loads `config.toml`. Relative paths in it resolve against the config file's directory, not the working directory.
- **sources.py**: returns raw rows (lists of cells) for each source `type`: `gsheet_api` (default; Drive discovery by `spreadsheet_title_pattern`, then falls back to a public XLSX download), `gsheet_csv`, or `file` (.csv/.xlsx). Short `M/D` API dates get their year from the workbook title here. Tabs in `skip_worksheets` are skipped. To add a new source type, add a reader here and a branch in `read_rows`.
- **parsing.py**: finds header rows, maps the configured `[columns]` to fields, and parses dates and amounts. It skips summary, zero and total rows and ignores side panels next to the data. Rows that look like expenses but won't parse are returned as failures (written to `parse_failures.csv`, overwritten each sync).
- **Row identity** (`parsing.assign_row_keys`): `row_key` = sha1 of the normalized content plus the occurrence index among identical rows. Dedup depends on this. Repeat syncs never duplicate rows, identical same-day transactions are kept separately, and an edited row becomes a new row (the old one stays unless `--mirror` is used).
- **sync.py**: by default the sync is incremental from `MAX(date)` in the DB, with that boundary date included. `--full-rescan` scans everything. `--mirror` also deletes DB rows within the source's date range whose keys are gone, which cascades to their additional labels. `--dry-run` may create the schema but writes no expenses and no `sync_runs` row.
- **statements.py**: parses bank statements (header found by column-name prefixes, since the `.txt` export truncates headers). It keeps only withdrawals, with `category = ""` (no Label), and keys them by date, ref, amount and closing balance so that every export format and overlapping statement produces the same `row_key`.
- **Two origins**: `expenses.origin` is `'sheet'` or `'bank'`. Anything that works per source must filter on it: the incremental boundary (`MAX(date)` of sheet rows), mirror deletions, and statement reconciliation. A sheet row and a bank withdrawal with the same date and whole-rupee amount (`sync.same_spend_key`) are the same spend, and the sheet wins. `sync_statements` skips bank rows covered by sheet rows (one-to-one, in statement order), and `sync.sync` deletes a matching bank row when the sheet row arrives later, moving its additional labels across.
- **Missing Label**: `labels.MISSING_LABELS` (`""`, `"Uncategorized"`) is the only state in which the Label can be set (`labels set-primary`, `POST /api/labels` with `action: "set_primary"`). The dashboard receives the list as `DATA.missingLabels` and shows those rows as "Unlabeled".
- **db.py**: holds the schema (`expenses`, `expense_labels`, `sync_runs`) and connection settings (WAL, foreign keys, full sync). Schema changes must be additive, with a migration in `initialize_database` for existing DBs (see how the `year` column is backfilled).
- **labels.py**: "additional labels" are user tags stored in `expense_labels`, kept separate from the workbook's `Label` (`expenses.category`). The CLI and the server share this module.
- **report.py / server.py**: `report.load` produces compact row tuples that the template's JS unpacks by position (`ROWS = DATA.rows.map(r => ...)` in `dashboard.html`). If you change the tuple shape, update both sides. `render_dashboard` injects the JSON by replacing the `/*__DATA__*/null` placeholder. `server.py` serves the same page with `editable: true`, plus `POST /api/labels`. It is meant to stay bound to localhost.

Naming mismatch: the DB columns `category` and `description` hold what the Sheet and dashboard call **Label** and **Item**.

Library code raises `ExpenseTrackerError` for problems the user can fix. Only `cli.py` turns those into exit messages.

## Local files not to touch or commit

`service_account.json` (despite its name, this is an OAuth Desktop client file), `authorized_user.json` (the cached token), `expenses.db*`, `dashboard.html` and `parse_failures.csv` are all gitignored local data.
