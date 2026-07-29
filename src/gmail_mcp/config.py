"""Config loader. Reads config.json and resolves account aliases to accounts.

An account entry may be either:
  - a bare email string (legacy form), which is treated as a Gmail account, or
  - an object {"email": ..., "provider": "gmail" | "outlook" | "imap"}.

This keeps existing single-provider configs working unchanged while allowing
per-account providers for mixed Gmail / Microsoft 365 / IMAP setups.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


CONFIG_ENV_VAR = "GMAIL_MCP_CONFIG"
USER_CONFIG_PATH = Path.home() / ".gmail-mcp" / "config.json"
REPO_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config.json"

SUPPORTED_PROVIDERS = ("gmail", "outlook", "imap")


class ConfigError(Exception):
    """Raised when config is missing or malformed."""


def config_path() -> Path:
    """Resolve which config.json to read.

    Order: $GMAIL_MCP_CONFIG, then ~/.gmail-mcp/config.json, then the
    repo-local config.json. The repo-local fallback keeps existing clones
    working; the home-directory location is what makes an installed copy
    (pip/uvx) usable, since there is no repo directory in that case.
    """
    override = os.environ.get(CONFIG_ENV_VAR)
    if override:
        return Path(override).expanduser()
    if USER_CONFIG_PATH.exists():
        return USER_CONFIG_PATH
    return REPO_CONFIG_PATH


def load_config() -> dict:
    path = config_path()
    if not path.exists():
        raise ConfigError(
            f"config.json not found. Create it at {USER_CONFIG_PATH} "
            f"(or set {CONFIG_ENV_VAR} to its path). "
            "See config.json.example for the format."
        )
    with path.open() as f:
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
        result = {"email": email, "provider": provider}
        for key in ("preset", "imap_host", "imap_port", "smtp_host", "smtp_port"):
            if key in value:
                result[key] = value[key]
        return result
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
    """Return the provider ('gmail', 'outlook' or 'imap') for an alias."""
    return resolve_account(alias)["provider"]
