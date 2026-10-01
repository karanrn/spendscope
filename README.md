# Expense tracker: Google Sheets → SQLite → dashboard

This project imports all accessible yearly Google spreadsheets whose titles match `Expenses YYYY` (for example, `Expenses 2025`), with one expense worksheet per month, into a durable SQLite database and builds a searchable dashboard.

- `sync.py` reads every monthly worksheet through the Google Sheets API by default, imports transactions, and skips summary/formula rows that do not match the expense columns. If API access is unavailable, it attempts to download the whole spreadsheet as XLSX and parse all tabs (requires the spreadsheet to be publicly viewable).
- `report.py` builds `dashboard.html` with monthly totals, Label and payment breakdowns, a searchable transaction list, and date/Label/payment/additional-label filters.
- `serve.py` runs the dashboard locally with controls to add/remove additional labels on each transaction; changes are written to SQLite immediately.
- `labels.py` attaches your own extra labels to transactions without changing the workbook’s existing Label values.

The expected transaction columns on each month sheet are:

| Date | Expense | Item | Label |
|---|---:|---|---|
| 2025-01-02 | 450 | Coffee and sandwich | Food |

Header unit suffixes such as `Expense(INR)` are accepted as `Expense`. Sheets API dates formatted as `M/D` without a year are interpreted as month/day and get the year from the parent `Expenses YYYY` spreadsheet. Worksheets named in `source.skip_worksheets` (default `Summary`) are not read. Side panels next to the expense columns (such as a monthly `Total` cell) are ignored, so the expense on the same row is still imported. Zero-amount rows are skipped. Rows that look like expenses but have an unreadable date or amount are written to `parse_failures.csv` (`parsing.failures_log`), which is overwritten on every sync.

## Setup

1. In the same Google Cloud project, enable both the **Google Sheets API** and the **Google Drive API**, configure the OAuth consent screen, and create an OAuth Client ID for a **Desktop app**. Drive API is used to discover spreadsheets by title. Save the downloaded client JSON in the project folder. The supplied `service_account.json` is an installed-app OAuth client file, not a service-account key or an OAuth access token.
2. Install the API client and workbook reader (Python 3.11+):
   ```bash
   python -m pip install -r requirements.txt
   ```
3. Set `oauth_client_json` to the downloaded OAuth client file and `oauth_token_json` to a writable path for the cached token (defaults to `authorized_user.json`). The first sync opens a browser for you to authorize the Google account. Share each yearly spreadsheet with that same account as a Viewer.
4. The default `spreadsheet_title_pattern` discovers every accessible spreadsheet titled `Expenses YYYY`; leave it as-is for that naming convention. Optionally set `folder_id` to limit discovery to a Drive folder. Leave `worksheet = ""` to import every month tab in every matching yearly file. If Drive title discovery is unavailable, set `sheet_id` to a specific spreadsheet ID to use Sheets API access without Drive discovery. `sheet_url` also enables public XLSX fallback for that one workbook; public sharing is required for that fallback.
5. Check parsing, import data, and build the dashboard:
   ```bash
   make dry-run
   make run
   ```

You can also run `make sync` to import without opening the UI, or `make serve` to start the dashboard against the existing database. Set `PYTHON` when needed, for example `make run PYTHON=.venv/bin/python`.

The default `config.toml` uses read-only OAuth access to Google Sheets and maps Date → date, Expense → amount, Item → transaction item/description, and Label → the existing expense label. The OAuth user token is cached locally so later syncs (including scheduled ones) can refresh authorization without a service account. New annual spreadsheets are picked up automatically when shared with the authorized Google account and titled `Expenses YYYY`. To change the naming convention, edit `spreadsheet_title_pattern`. To import a local workbook or CSV instead, change `[source].type` to `"file"`; see “Other sources” below.

`serve.py` opens the editable dashboard at `http://127.0.0.1:8765`. On each transaction, type a custom label and choose **Add**; click an existing custom-label chip to remove it. Changes are saved immediately to the configured SQLite database and reflected in the dashboard filters. Keep the server bound to localhost; it is intended for personal use.

`report.py` is the static-export alternative: it writes a standalone `dashboard.html` snapshot that can be archived, copied, or served by a static web host. It has no server/API connection, so it is view-only and label edits are disabled. `serve.py` renders the dashboard dynamically and adds the local API needed to persist label edits; you do not need to run `report.py` for normal interactive use. Use `make report` or `make report-open` when you specifically need the static snapshot.

Do not commit or share OAuth client files or the cached token. The downloaded client file is not itself the user authorization token; `gspread` writes that token to `oauth_token_json` after the first browser login. If credentials have been exposed, revoke/rotate the OAuth client in Google Cloud and authorize again.

## Durable database and restart recovery

`expenses.db` is a regular SQLite database file on disk, not temporary/in-memory storage. On startup, `sync.py` creates the configured parent directory, opens or creates the database, creates the tables and indexes, and applies additive schema changes needed by newer versions. SQLite uses a write-ahead log, foreign keys, a busy timeout, and full synchronous commits. Committed expenses and added labels therefore remain available after the script exits or the computer restarts.

Keep `expenses.db` (and, while a sync is active, its SQLite `-wal`/`-shm` files) on persistent local storage. For backups, use SQLite’s backup feature or copy the database while scripts are stopped; do not store the database in a temporary directory.

The database contains:

- `expenses`: transaction date, `month` (`YYYY-MM`), `year` (calendar year), amount, source Label, Item, optional fields, and import metadata. The year is derived from the transaction date and backfilled automatically for existing databases.
- `expense_labels`: additional labels attached to expense records; they remain separate from the workbook’s original Label.
- `sync_runs`: history and counts for each successful sync.

## Incremental sync and corrections

Every regular run reads the source and imports transactions from the **latest date already stored in the database onward**. The boundary date is included, so more expenses added on that same date are still picked up. Rows before that date are not re-imported during a regular run. On a brand-new database, the full workbook is imported.

If you add a late expense with an older date or need to rescan the full history, run:

```bash
python sync.py --full-rescan
```

Identical transactions on the same date are retained by occurrence, while repeating a sync does not duplicate them. A changed transaction is treated as a new version by default; the old version remains. `--mirror` instead rescans the full source and removes database rows within the source’s covered date range that no longer match it. Use mirror only when the source is authoritative for that whole date range; user-added secondary labels for removed transactions go away with those transactions.

`--dry-run` reports what would be inserted without writing. It may initialize an empty database/schema so the configured path can be checked, but it does not save transactions or a sync-history row.

## Add secondary labels to existing Labels

For individual transactions, use the editable dashboard described above. To bulk-assign labels from the command line, use `labels.py` without replacing the workbook Label. Match by existing Label, and optionally narrow the selection by text contained in Item and by date range:

```bash
python labels.py add Work --source-label Food --item-contains coffee
python labels.py add Reimbursable --source-label Travel --from 2025-01-01 --to 2025-12-31
python labels.py list
python labels.py remove Work --source-label Food --item-contains coffee
```

Label matching is case-insensitive; Item matching is case-insensitive substring search. Dates use `YYYY-MM-DD`. Assignments are stored in SQLite, survive restarts and later syncs, and can be selected in the dashboard’s Additional label filter. Existing workbook Labels remain unchanged.

## Search Items and query SQLite

The dashboard search box searches Item text, source Label, payment, and added labels; entering part of an Item name filters matching transactions. You can also query the database directly (with the `sqlite3` command-line program):

```sql
-- Find transactions whose Item contains “coffee” (case-insensitive)
SELECT date, amount, category AS label, description AS item
FROM expenses
WHERE instr(lower(coalesce(description, '')), 'coffee') > 0
ORDER BY date DESC;

-- Totals by month and source Label
SELECT month, category AS label, ROUND(SUM(amount), 2) AS spent
FROM expenses GROUP BY month, category ORDER BY month, spent DESC;

-- Totals by calendar year and source Label
SELECT year, category AS label, ROUND(SUM(amount), 2) AS spent
FROM expenses GROUP BY year, category ORDER BY year, spent DESC;

-- Transactions with a specific additional label
SELECT e.date, e.amount, e.category AS label, e.description AS item
FROM expenses AS e
JOIN expense_labels AS l ON l.expense_id = e.id
WHERE l.label = 'Work' COLLATE NOCASE
ORDER BY e.date DESC;

-- Recent sync history
SELECT * FROM sync_runs ORDER BY id DESC LIMIT 5;
```

## Run it periodically

On macOS/Linux, schedule sync and dashboard generation (for example, nightly at 11 pm) with `crontab -e`:

```cron
0 23 * * * cd /path/to/expense-tracker && /usr/bin/python3 sync.py && /usr/bin/python3 report.py >> sync.log 2>&1
```

On Windows, use Task Scheduler to run `python sync.py` and then `python report.py`, with the project folder as “Start in”.

## Other sources

- **CSV file:** set `type = "file"` and set `file_path` to the CSV. Update `[columns]` to match its header names.
- **Public Google Sheet without API credentials:** set `type = "gsheet_csv"` and `sheet_url` to export one tab as CSV. For the default API mode’s XLSX fallback, the spreadsheet must be publicly viewable and `sheet_id` must be configured.
- **Google Sheets API:** the default `type = "gsheet_api"` uses the OAuth Desktop client and cached user token. The authorized Google account must have Viewer access to each yearly spreadsheet. API access works for private spreadsheets; the XLSX download fallback does not.
- **Excel:** `.xlsx` and `.xlsm` files need `openpyxl` (included in `requirements.txt`). Set `type = "file"`, set `file_path`, and leave `file_sheet = ""` to scan all tabs.

To try the included CSV sample, set `type = "file"`, set `file_path = "sample_expenses.csv"`, and change the `[columns]` mappings to Date, Amount, Category, Description, Payment Method, and (if needed) Notes before syncing.
