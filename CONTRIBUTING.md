# Contributing

Thanks for helping. Bug reports, support for more banks, and small focused pull requests are all welcome.

## Setting up

```bash
git clone https://github.com/karanrn/spendscope && cd spendscope
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e .
python -m unittest discover -s tests -t .          # or: make test
python -m spendscope demo                     # the dashboard on made-up data
```

The tests use only the standard library's `unittest` and need no network or Google account. They build a throwaway project with a CSV source (`tests/helpers.py`).

## Before changing code

[CLAUDE.md](CLAUDE.md) describes the architecture and the rules that keep people's existing databases working. The important ones:

- **Never change how `row_key` is computed** for sheet rows (`parsing.assign_row_keys`) or bank rows (`statements.assign_row_keys`). Keys of rows already stored would stop matching, and the next sync would import everything again as duplicates.
- **Schema changes must be additive**, with a migration in `db.initialize_database` for existing databases.
- **Anything per source filters on `expenses.origin`** (`sheet` or `bank`).
- **The dashboard server stays bound to localhost.** Any new route keeps the `Host` check and, for POSTs, the JSON and same-origin checks.
- Library code raises `SpendScopeError` for problems the user can fix. Only `cli.py` turns them into exit messages.
- If you change the row tuple in `report.load`, change its unpacking in `templates/dashboard.html` too.

No linter or formatter is configured. Match the surrounding style.

## Adding a bank

Most banks work already, because header cells are matched by pattern (`statements.FIELD_PATTERNS`). If one doesn't:

1. Get the redacted header and sample row from `python -m spendscope sync --statement FILE --show-headers`.
2. Prefer widening a pattern in `FIELD_PATTERNS` (the order matters, see its comment) over adding special cases.
3. Add a test in `tests/test_statements.py` with a **made-up** statement in that bank's layout. Never commit a real one.

## Pull requests

- Add or update tests for behaviour changes, and run the whole suite.
- Update README.md for anything users see, and add a line under "Unreleased" in [CHANGELOG.md](CHANGELOG.md).
- If you change the dashboard, check it in a light and a dark theme. If the README tour should show the change, extend `docs/media/record.mjs` and run `make media` (needs Node.js and ffmpeg).

## Releasing (maintainers)

1. Move the "Unreleased" notes in CHANGELOG.md under the new version, set `__version__` in `spendscope/__init__.py`, and update the version in the README's `pipx install` line.
2. Commit, then tag and push: `git tag v0.2.0 && git push origin main v0.2.0`.
3. The `release` workflow runs the tests, builds, and creates a GitHub Release for the tag with the wheel and sdist attached. Nothing is published to PyPI.
