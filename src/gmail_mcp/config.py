"""Config loader. Reads config.json and resolves account aliases to accounts.

An account entry may be either:
  - a bare email string (legacy form), which is treated as a Gmail account, or
  - an object {"email": ..., "provider": "gmail" | "outlook"}.

This keeps existing single-provider configs working unchanged while allowing
per-account providers for mixed Gmail / Microsoft 365 setups.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


# Both paths can be overridden with env vars, e.g. when the server runs in a
# container with config and tokens on a mounted volume.
CONFIG_PATH = Path(
    os.environ.get("GMAIL_MCP_CONFIG")
    or Path(__file__).resolve().parent.parent.parent / "config.json"
)

# Where tokens.db (and, for the remote connector, connector.db) live.
DATA_DIR = Path(os.environ.get("GMAIL_MCP_HOME") or Path.home() / ".gmail-mcp")

SUPPORTED_PROVIDERS = ("gmail", "outlook")


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


def _normalize_account(alias: str, value: object) -> dict:
    """Turn a raw config entry into {'email': ..., 'provider': ...}.

    Accepts a bare email string (legacy, => gmail) or an object with an
    'email' field and optional 'provider' field.
    """
    if isinstance(value, str):
        return {"email": value, "provider": "gmail"}
    if isinstance(value, dict):
        email = value.get("email")
        if not email or not isinstance(email, str):
            raise ConfigError(
                f"Account '{alias}' must have a string 'email' field."
            )
        provider = value.get("provider", "gmail")
        if provider not in SUPPORTED_PROVIDERS:
            raise ConfigError(
                f"Account '{alias}' has unknown provider '{provider}'. "
                f"Supported: {', '.join(SUPPORTED_PROVIDERS)}."
            )
        return {"email": email, "provider": provider}
    raise ConfigError(
        f"Account '{alias}' must be an email string or a "
        "{'email': ..., 'provider': ...} object."
    )


def resolve_account(alias: str) -> dict:
    """Return {'email': ..., 'provider': ...} for an alias. Raises if not found."""
    accounts = load_config()["accounts"]
    if alias not in accounts:
        raise ConfigError(
            f"Unknown account alias '{alias}'. Available: {', '.join(accounts)}"
        )
    return _normalize_account(alias, accounts[alias])


def resolve_alias(alias: str) -> str:
    """Turn 'personal' into 'you@gmail.com'. Raises if alias not found."""
    return resolve_account(alias)["email"]


def get_provider(alias: str) -> str:
    """Return the provider ('gmail' or 'outlook') for an alias."""
    return resolve_account(alias)["provider"]
