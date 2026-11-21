# Changelog

All notable changes are listed here. Versions follow [semantic versioning](https://semver.org/).

## Unreleased

## 0.2.0

Easier to set up and use outside India.

### Added
- `init` writes a starter `config.toml` (and, for a local file source, this year's workbook) after two questions: the source, and a region preset (`in`, `us`, `uk`, `eu`).
- `demo` builds a project with a year of made-up data, imports it and opens the dashboard.
- `doctor` checks the config, Python packages, Google credentials, source and database, and says what to fix.
- `template --year YYYY` writes the yearly workbook template for any year.
- `backup` copies the database safely, even while it's in use.
- `sync --statement FILE --show-headers` shows which columns were recognised, with transaction data redacted so it can be shared in an issue.
- `[rules]` in config.toml: Label → Item keywords, applied to expenses without a Label after every import and with `labels apply-rules`.
- `[statements] import_deposits = true` keeps statement deposits as income. The dashboard then shows income against spending per month.
- A "Download CSV" button for the transactions table, honouring the filters, the search and the month.
- `[display] locale` for number and month formatting in the dashboard, `[parsing] decimal_separator` (`","` reads `1.234,56`) and `[parsing] currency_code`.
- `file_path` accepts a glob such as `"Expenses *.xlsx"`, to read one workbook per year.
- The config is found from `--config`, then `$EXPENSE_TRACKER_CONFIG`, then `./config.toml`, then the per-user config (`~/.config/expense-tracker/config.toml`, or `%APPDATA%\expense-tracker` on Windows).
- `--version`, and packaging for PyPI as `sheets-expense-tracker` (`pipx install sheets-expense-tracker`).

### Changed
- Chart.js is bundled into the dashboard, so it works offline and makes no third-party requests.
- `config.toml` is no longer tracked in git. **If you cloned the repository, copy your `config.toml` somewhere safe before pulling**, because git deletes the tracked file. Copy it back afterwards. The example config now lives at `expense_tracker/config.example.toml`.
- The default OAuth client file is `oauth_client.json`. An existing `service_account.json` is still used when the config doesn't name one.
- The template workbook moved to `expense_tracker/templates/expenses-template.xlsx`. Use `python -m expense_tracker template` to get a copy.
- Help text and messages use `python -m expense_tracker …` throughout.

## 0.1.0

First release: Google Sheets and local file import, bank statements, additional labels, budgets, and the dashboard.
