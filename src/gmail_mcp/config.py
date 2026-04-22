"""Config loader. Reads config.json and resolves account aliases to email addresses."""
from __future__ import annotations

import json
from pathlib import Path


CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config.json"


class ConfigError(Exception):
    """Raised when config is missing or malformed."""


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise ConfigError(
            f"config.json not found at {CONFIG_PATH}. "
            "Copy config.json.example to config.json and fill in your accounts."
        )
    with CONFIG_PATH.open() as f:
        data = json.load(f)
    if "accounts" not in data or not isinstance(data["accounts"], dict):
        raise ConfigError("config.json must have an 'accounts' object mapping aliases to emails.")
    return data


def list_account_aliases() -> list[str]:
    return list(load_config()["accounts"].keys())


def resolve_alias(alias: str) -> str:
    """Turn 'personal' into 'you@gmail.com'. Raises if alias not found."""
    accounts = load_config()["accounts"]
    if alias not in accounts:
        raise ConfigError(
            f"Unknown account alias '{alias}'. Available: {', '.join(accounts)}"
        )
    return accounts[alias]
