"""`init`: write a starter config.toml (from config.example.toml) and, for a local file source,
a workbook from the template, so a first sync works without any further setup."""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

from expense_tracker import ExpenseTrackerError
from expense_tracker.config import DEFAULT_CONFIG, EXAMPLE_CONFIG, user_config_dir
from expense_tracker.template import workbook_name, write_template

SOURCES = {
    "file": "a local .xlsx/.csv workbook (no Google account needed)",
    "gsheet_csv": "a Google Sheet shared as 'Anyone with the link can view'",
    "gsheet_api": "private yearly Google Sheets (needs a Google Cloud OAuth client)",
}

# Region presets: (section, key) → value written into the config
PRESETS: dict[str, dict] = {
    "in": {"name": "India (₹, 1,00,000, day first)",
           ("parsing", "currency_symbol"): "₹", ("parsing", "currency_code"): "INR",
           ("parsing", "decimal_separator"): ".", ("display", "locale"): "en-IN"},
    "us": {"name": "United States ($, 100,000, month first)",
           ("parsing", "currency_symbol"): "$", ("parsing", "currency_code"): "USD",
           ("parsing", "decimal_separator"): ".", ("display", "locale"): "en-US",
           ("parsing", "date_formats"): ["%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%m-%d-%Y", "%b %d, %Y"],
           ("statements", "date_formats"): ["%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y"]},
    "uk": {"name": "United Kingdom (£, 100,000, day first)",
           ("parsing", "currency_symbol"): "£", ("parsing", "currency_code"): "GBP",
           ("parsing", "decimal_separator"): ".", ("display", "locale"): "en-GB"},
    "eu": {"name": "Euro area (€, 100.000,00, day first)",
           ("parsing", "currency_symbol"): "€", ("parsing", "currency_code"): "EUR",
           ("parsing", "decimal_separator"): ",", ("display", "locale"): "de-DE",
           ("parsing", "date_formats"): ["%d.%m.%Y", "%d.%m.%y", "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"],
           ("statements", "date_formats"): ["%d.%m.%Y", "%d.%m.%y"]},
}


def toml_value(value) -> str:
    """A TOML literal for a string or a list of strings (JSON's quoting is valid TOML here)."""
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(v) for v in value) + "]"
    return json.dumps(value, ensure_ascii=False)


def set_key(text: str, section: str, key: str, value) -> str:
    """Replace `key = …` in [section] of a TOML text, keeping any trailing comment."""
    lines, current, done = text.splitlines(keepends=True), "", False
    for i, line in enumerate(lines):
        header = re.match(r"^\s*\[([^\]]+)\]\s*(#.*)?$", line)
        if header:
            current = header.group(1).strip()
            continue
        m = re.match(rf"^(\s*{re.escape(key)}\s*=\s*)(.*?)(\s+#.*)?(\r?\n)?$", line)
        if current == section and m:
            lines[i] = m.group(1) + toml_value(value) + (m.group(3) or "") + (m.group(4) or "")
            done = True
            break
    if not done:
        raise ValueError(f"{key} not found in [{section}] of the example config")
    return "".join(lines)


def ask(prompt: str, choices: dict[str, str], default: str) -> str:
    print(prompt)
    keys = list(choices)
    for n, key in enumerate(keys, 1):
        print(f"  {n}. {choices[key]}" + ("  (default)" if key == default else ""))
    while True:
        answer = input("> ").strip().lower()
        if not answer:
            return default
        if answer.isdigit() and 1 <= int(answer) <= len(keys):
            return keys[int(answer) - 1]
        if answer in choices:
            return answer
        print(f"Enter a number from 1 to {len(keys)}.")


def init_project(config_path: Path | None = None, source: str | None = None, preset: str | None = None,
                 global_config: bool = False, force: bool = False,
                 interactive: bool | None = None) -> list[Path]:
    """Write config.toml (and a starter workbook for the file source); returns what was written."""
    if config_path is None:
        config_path = (user_config_dir() if global_config else Path.cwd()) / DEFAULT_CONFIG
    config_path = config_path.expanduser().resolve()
    if config_path.exists() and not force:
        raise ExpenseTrackerError(f"{config_path} already exists; pass --force to replace it.")
    if interactive is None:
        interactive = sys.stdin.isatty() and (source is None or preset is None)
    if interactive and source is None:
        source = ask("Where do you keep your expenses?", SOURCES, "file")
    if interactive and preset is None:
        preset = ask("Which currency and number format?", {k: v["name"] for k, v in PRESETS.items()}, "in")
    source, preset = source or "file", preset or "in"
    if source not in SOURCES:
        raise ExpenseTrackerError(f"Unknown source {source!r}; choose one of {', '.join(SOURCES)}")
    if preset not in PRESETS:
        raise ExpenseTrackerError(f"Unknown region {preset!r}; choose one of {', '.join(PRESETS)}")

    text = set_key(EXAMPLE_CONFIG.read_text(encoding="utf-8"), "source", "type", source)
    for where, value in PRESETS[preset].items():
        if where != "name":
            text = set_key(text, *where, value)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(text, encoding="utf-8")
    written = [config_path]

    year = date.today().year
    workbook = config_path.parent / workbook_name(year)
    if source == "file" and not workbook.exists():
        written.append(write_template(workbook, year, currency_code=PRESETS[preset][("parsing", "currency_code")]))
    print("Wrote " + ", ".join(str(p) for p in written))
    print_next_steps(source, config_path, workbook)
    return written


def print_next_steps(source: str, config_path: Path, workbook: Path) -> None:
    found_anyway = {(Path.cwd() / DEFAULT_CONFIG).resolve(), (user_config_dir() / DEFAULT_CONFIG).resolve()}
    flag = "" if config_path in found_anyway else f" --config '{config_path}'"
    print("\nNext steps:")
    if source == "file":
        print(f"  1. Add a few expenses to {workbook.name} (Date, Expense, Item, Label), one tab per month.")
    elif source == "gsheet_csv":
        print("  1. Share your sheet as 'Anyone with the link can view' and put its link in sheet_url.")
    else:
        print("  1. Create a Google Cloud OAuth client (Desktop app) and save its JSON as "
              f"{config_path.parent / 'oauth_client.json'}.\n"
              "     The README's 'Connecting Google Sheets' section walks through it.")
    print(f"  2. Check the setup:        python -m expense_tracker doctor{flag}")
    print(f"  3. Import and explore:     python -m expense_tracker sync{flag}"
          f" && python -m expense_tracker serve{flag}")
