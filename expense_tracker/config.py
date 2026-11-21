"""Find and load config.toml, fill defaults, and resolve its relative paths against the config file."""
from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path

from expense_tracker import ExpenseTrackerError

DEFAULT_CONFIG = "config.toml"
ENV_VAR = "EXPENSE_TRACKER_CONFIG"
EXAMPLE_CONFIG = Path(__file__).resolve().parent / "config.example.toml"

# (section, key) entries holding file paths; relative values are relative to the config file
PATH_KEYS = (
    ("database", "path"),
    ("source", "file_path"),
    ("source", "oauth_client_json"),
    ("source", "oauth_token_json"),
    ("parsing", "failures_log"),
)
DEFAULT_COLUMNS = {"date": "Date", "amount": "Expense", "category": "Label", "description": "Item",
                   "payment": "", "subcategory": ""}
OAUTH_CLIENT = "oauth_client.json"
LEGACY_OAUTH_CLIENT = "service_account.json"  # the old default name for the same OAuth client file


def user_config_dir() -> Path:
    """Per-user folder for a config that works from any directory (used by `init --global`)."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "expense-tracker"


def config_candidates() -> list[Path]:
    """Where a config is looked for when --config isn't given, in order."""
    found = [Path(os.environ[ENV_VAR]).expanduser()] if os.environ.get(ENV_VAR) else []
    return found + [Path(DEFAULT_CONFIG), user_config_dir() / DEFAULT_CONFIG]


def find_config(path: str | Path | None = None) -> Path:
    """The config file to use: an explicit path, $EXPENSE_TRACKER_CONFIG, ./config.toml, then the
    per-user config."""
    if path:
        config_path = Path(path).expanduser().resolve()
        if not config_path.exists():
            raise ExpenseTrackerError(f"Config not found: {config_path}")
        return config_path
    candidates = config_candidates()
    if os.environ.get(ENV_VAR):
        if not candidates[0].exists():
            raise ExpenseTrackerError(f"Config not found: {candidates[0]} (from ${ENV_VAR})")
        return candidates[0].resolve()
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    looked = ", ".join(str(c) for c in candidates)
    raise ExpenseTrackerError(
        f"No config.toml found (looked in {looked}).\n"
        "Create one with:  python -m expense_tracker init\n"
        "or try the tool on made-up data first:  python -m expense_tracker demo")


def load_config(path: str | Path | None = None) -> dict:
    """Read the config, fill defaults, and make every configured path absolute."""
    config_path = find_config(path)
    try:
        cfg = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ExpenseTrackerError(f"{config_path} is not valid TOML: {exc}") from exc
    base = config_path.parent
    cfg["_path"] = str(config_path)
    source = cfg.setdefault("source", {})
    if "oauth_client_json" not in source:
        legacy = base / LEGACY_OAUTH_CLIENT
        source["oauth_client_json"] = (LEGACY_OAUTH_CLIENT if legacy.exists() and not (base / OAUTH_CLIENT).exists()
                                       else OAUTH_CLIENT)
    source.setdefault("oauth_token_json", "authorized_user.json")
    cfg.setdefault("database", {}).setdefault("path", "expenses.db")
    cfg["columns"] = {**DEFAULT_COLUMNS, **cfg.get("columns", {})}
    cfg.setdefault("parsing", {}).setdefault("failures_log", "parse_failures.csv")
    decimal = cfg["parsing"].get("decimal_separator", ".")
    if decimal not in (".", ","):
        raise ExpenseTrackerError(f'parsing.decimal_separator must be "." or ",", not {decimal!r}')
    for section, key in PATH_KEYS:
        value = cfg.get(section, {}).get(key)
        if value and not Path(value).expanduser().is_absolute():
            cfg[section][key] = str(base / value)
        elif value:
            cfg[section][key] = str(Path(value).expanduser())
    return cfg


def database_path(cfg: dict) -> Path:
    return Path(cfg["database"]["path"])


def currency_symbol(cfg: dict) -> str:
    return cfg.get("parsing", {}).get("currency_symbol", "₹")


def display_locale(cfg: dict) -> str:
    """BCP 47 locale for numbers and month names in the dashboard, such as en-IN or en-US."""
    return cfg.get("display", {}).get("locale", "en-IN")


def rules(cfg: dict) -> dict[str, list[str]]:
    """[rules]: Label → Item keywords, in config order. A string value is one keyword."""
    out: dict[str, list[str]] = {}
    for label, keywords in cfg.get("rules", {}).items():
        words = [keywords] if isinstance(keywords, str) else keywords
        if not isinstance(words, list) or not all(isinstance(w, str) for w in words):
            raise ExpenseTrackerError(f"[rules] {label!r} must be a keyword or a list of keywords")
        words = [w.strip() for w in words if w.strip()]
        if words:
            out[label] = words
    return out
