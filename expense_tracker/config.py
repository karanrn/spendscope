"""Load config.toml and resolve its relative paths against the config file."""
from __future__ import annotations

import tomllib
from pathlib import Path

from expense_tracker import ExpenseTrackerError

DEFAULT_CONFIG = "config.toml"

# (section, key) entries holding file paths; relative values are relative to the config file
PATH_KEYS = (
    ("database", "path"),
    ("source", "file_path"),
    ("source", "oauth_client_json"),
    ("source", "oauth_token_json"),
    ("parsing", "failures_log"),
)


def load_config(path: str | Path = DEFAULT_CONFIG) -> dict:
    """Read the config, fill defaults, and make every configured path absolute."""
    config_path = Path(path).expanduser().resolve()
    if not config_path.exists():
        raise ExpenseTrackerError(f"Config not found: {config_path}")
    cfg = tomllib.loads(config_path.read_text(encoding="utf-8"))
    cfg.setdefault("source", {})
    cfg.setdefault("database", {}).setdefault("path", "expenses.db")
    cfg.setdefault("parsing", {}).setdefault("failures_log", "parse_failures.csv")
    base = config_path.parent
    for section, key in PATH_KEYS:
        value = cfg.get(section, {}).get(key)
        if value and not Path(value).expanduser().is_absolute():
            cfg[section][key] = str(base / value)
    return cfg


def database_path(cfg: dict) -> Path:
    return Path(cfg["database"]["path"])


def currency_symbol(cfg: dict) -> str:
    return cfg.get("parsing", {}).get("currency_symbol", "₹")
