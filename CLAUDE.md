# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A tool, shared publicly, that imports yearly expense workbooks (titled `Expenses YYYY`, one tab per month; Google Sheets or local `.xlsx`/`.csv`) into SQLite, then shows the data in a local dashboard. Python 3.11+, standard library plus `gspread`, `openpyxl` and `xlrd` (legacy `.xls` bank statements). Bank account statements can also be imported (`sync --statement FILE…`). `README.md` covers user setup, the Sheet template layout, and example SQL; `CONTRIBUTING.md` covers contributing and releasing. Named SpendScope: the package, the command and the import name are all `spendscope`. It is distributed through GitHub Releases (wheel + sdist attached for each `v*` tag), not PyPI; nothing is published on the PyPI index.

## Commands

```bash
make test                       # python -m unittest discover -s tests -t .
python -m unittest tests.test_sync                                   # one module
python -m unittest tests.test_sync.SyncTests.test_dry_run_writes_no_expenses  # one test

python -m spendscope demo --dir /tmp/demo --no-serve   # made-up data project to try commands against
python -m spendscope doctor --config /tmp/demo/config.toml

make dry-run    # sync --dry-run: parse the source and preview inserts without writing
make statement FILES='Acct_*.xls'   # sync --statement: import bank statements
make sync       # import into SQLite
make serve      # editable dashboard at http://127.0.0.1:8765
make report     # static, view-only dashboard.html
make run PYTHON=.venv/bin/python   # override the interpreter (a .venv exists locally)
make build     # wheel + sdist into dist/ (needs `pip install build`)
make media     # re-record docs/spendscope-demo.mp4 and docs/screenshots (node + ffmpeg, ~2 min)
```

All entry points go through `python -m spendscope {init,demo,doctor,sync,serve,report,labels,budgets,template,backup}` (`spendscope/cli.py`). `__version__` in `spendscope/__init__.py` is the single version source (pyproject reads it; the release workflow checks the tag matches). The top-level `sync.py`, `serve.py`, `report.py` and `labels.py` are thin shims kept so existing commands and cron jobs keep working. Don't put logic in them.

There is no linter or formatter configured. Tests use only `unittest` and need no network or Google credentials. They build a temp project with a CSV source and config (`tests/helpers.py`).

## Architecture

Pipeline: `sources.read_rows` → `parsing.to_records` → `sync.sync` → SQLite (`db.py`) → `report.load` → `templates/dashboard.html`. Bank statements take a parallel path: `statements.read_statements` → `sync.sync_statements`.

- **config.py**: finds and loads `config.toml`: `--config`, then `$SPENDSCOPE_CONFIG`, then `./config.toml`, then the per-user config (`user_config_dir()`). Relative paths in it resolve against the config file's directory, not the working directory. It fills defaults (columns, OAuth file names: `oauth_client.json`, falling back to a legacy `service_account.json`). `config.toml` is gitignored; the documented example ships in the package as `spendscope/config.example.toml`, which `scaffold.init_project` (`init`) copies and edits key by key (`set_key`), so keep its keys present and in their sections.
- **sources.py**: returns raw rows (lists of cells) for each source `type`: `gsheet_api` (Drive discovery by `spreadsheet_title_pattern`, then falls back to a public XLSX download), `gsheet_csv`, or `file` (.csv/.xlsx; `file_path` may be a glob, read in sorted order). Short `M/D` API dates get their year from the workbook title here. Tabs in `skip_worksheets` are skipped. To add a new source type, add a reader here and a branch in `read_rows`.
- **parsing.py**: finds header rows, maps the configured `[columns]` to fields, and parses dates and amounts (`decimal_separator`, `currency_code`, common currency signs). It skips summary, zero and total rows and ignores side panels next to the data. Rows that look like expenses but won't parse are returned as failures (written to `parse_failures.csv`, overwritten each sync).
- **Row identity** (`parsing.assign_row_keys`): `row_key` = sha1 of the normalized content plus the occurrence index among identical rows. Dedup depends on this. Repeat syncs never duplicate rows, identical same-day transactions are kept separately, and an edited row becomes a new row (the old one stays unless `--mirror` is used).
- **sync.py**: by default the sync is incremental from `MAX(date)` in the DB, with that boundary date included. `--full-rescan` scans everything. `--mirror` also deletes DB rows within the source's date range whose keys are gone, which cascades to their additional labels. `--dry-run` may create the schema but writes no expenses and no `sync_runs` row.
- **statements.py**: parses bank statements from any bank. Each header cell is classified by `FIELD_PATTERNS`, and the order of that list matters ("Value Date" before date, "Withdrawal Amount" as a debit before amount). `[statements.columns]` overrides take priority. Text files are tried with each delimiter, and the table where a header row is found wins. Withdrawals become expenses with `category = ""` (no Label), keyed by date, ref, amount and closing balance (prefix `bank|`), so every export format and overlapping statement gives the same `row_key`. Don't change that key: it would duplicate rows that are already imported. Deposits are returned separately (prefix `bank-in|`) and stored in the `income` table only with `[statements] import_deposits = true`. `describe_statement` (`sync --statement F --show-headers`) prints the recognised columns with data masked by `redact`, for bug reports.
- **Two origins**: `expenses.origin` is `'sheet'` or `'bank'`. Anything that works per source must filter on it: the incremental boundary (`MAX(date)` of sheet rows), mirror deletions, and statement reconciliation. A sheet row and a bank withdrawal with the same date and whole-rupee amount (`sync.same_spend_key`) are the same spend, and the sheet wins. `sync_statements` skips bank rows covered by sheet rows (one-to-one, in statement order), and `sync.sync` deletes a matching bank row when the sheet row arrives later, moving its additional labels across.
- **Missing Label**: `labels.MISSING_LABELS` (`""`, `"Uncategorized"`) is the only state in which the Label can be set (`labels set-primary`, `POST /api/labels` with `action: "set_primary"`). The dashboard receives the list as `DATA.missingLabels` and shows those rows as "Unlabeled".
- **db.py**: holds the schema (`expenses`, `expense_labels`, `budgets`, `sync_runs`) and connection settings (WAL, foreign keys, full sync). Schema changes must be additive, with a migration in `initialize_database` for existing DBs (see how the `year` column is backfilled).
- **labels.py**: "additional labels" are user tags stored in `expense_labels`, kept separate from the workbook's `Label` (`expenses.category`). The CLI and the server share this module. `apply_rules` gives missing Labels from `[rules]` (Label → Item keywords, first match wins, `config.rules`); `sync.apply_rules` runs it after every sheet sync and statement import. It only changes `category` in the DB, never a `row_key`.
- **budgets.py**: monthly budgets per Label, keyed by `(label, month)` with `label` NOCASE. `month = ''` applies to every month, and a `YYYY-MM` entry overrides it for that month only. The CLI (`budgets {list,set,remove}`) and `POST /api/budgets` (`amount: null` removes) share it. The dashboard gets the raw entries as `DATA.budgets` and resolves the effective budget per month in JS (`budgetsFor`), counting all expenses in the month regardless of filters.
- **report.py / server.py**: the page inlines Chart.js from `templates/chart.umd.min.js` (placeholder `/*__CHARTJS__*/`), so it makes no network requests; don't reintroduce a CDN. `DATA.locale` (`[display] locale`) drives all number/month formatting in the JS, and `DATA.income` feeds the income card. `report.load` produces compact row tuples that the template's JS unpacks by position (`ROWS = DATA.rows.map(r => ...)` in `dashboard.html`). If you change the tuple shape, update both sides. `render_dashboard` injects the JSON by replacing the `/*__DATA__*/null` placeholder. `server.py` serves the same page with `editable: true`, plus `POST /api/labels` and `POST /api/budgets`. It is meant to stay bound to localhost. Every request must carry an allowed `Host` (loopback names, against DNS rebinding), and POSTs must be `application/json` with a same-origin `Origin` if one is sent (against cross-site writes). Keep both checks on any new route.

Naming mismatch: the DB columns `category` and `description` hold what the Sheet and dashboard call **Label** and **Item**.

Library code raises `SpendScopeError` for problems the user can fix. Only `cli.py` turns those into exit messages.

- **Onboarding modules**: `scaffold.py` (`init`, region presets), `demo.py` (made-up data; `build_demo` refuses to replace a non-empty folder without its `.spendscope-demo` marker), `doctor.py` (checks; never starts the Google sign-in itself), `template.py` (writes the bundled `templates/expenses-template.xlsx` for any year by renaming the `MonYY` tabs in the xlsx XML, because an openpyxl round-trip drops the Summary chart). `db.backup` uses SQLite's online backup API.

## README media

`make media` runs `docs/media/build.sh`: `make_demo.py` builds a demo project with `spendscope.demo.build_demo` (January to `DEMO_TODAY`, no extras) in `docs/media/build/`, `capture.py` runs the real commands for the terminal scene, `record.mjs` (Playwright) records the terminal and a scripted dashboard tour, and `shots.mjs` takes the screenshots and thumbnail. It copies the results into `docs/` and updates the video length in README.md. The demo date is pinned (`DEMO_TODAY` in `make_demo.py`, applied to the browser clock). When dashboard features change, extend the tour in `record.mjs` (it uses element IDs from `dashboard.html`) and the video description in README.md.

## Local files not to touch or commit

`config.toml`, `oauth_client.json` / `service_account.json` (the OAuth Desktop client file under its new and old names), `authorized_user.json` (the cached token), `expenses.db*`, `backups/`, `dashboard.html` and `parse_failures.csv` are all gitignored local data.
