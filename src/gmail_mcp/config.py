"""Config loader. Finds and reads config.json and resolves account aliases to accounts.

An account entry may be either:
  - a bare email string (legacy form), which is treated as a Gmail account, or
  - an object {"email": ..., "provider": "gmail" | "outlook"}.

This keeps existing single-provider configs working unchanged while allowing
per-account providers for mixed Gmail / Microsoft 365 setups.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


CONFIG_ENV_VAR = "GMAIL_MCP_CONFIG"
DEFAULT_CONFIG_PATH = Path.home() / ".gmail-mcp" / "config.json"
# Pre-packaging location (repo root, next to pyproject.toml). Still honoured so
# existing clone-based installs keep working.
LEGACY_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config.json"

SUPPORTED_PROVIDERS = ("gmail", "outlook")

EXAMPLE_CONFIG = {
    "accounts": {
        "personal": "you@gmail.com",
        "work": {"email": "you@company.com", "provider": "outlook"},
    }
}


class ConfigError(Exception):
    """Raised when config is missing or malformed."""


_warned_legacy = False


def find_config_path() -> Path:
    """Locate config.json.

    Lookup order:
      1. $GMAIL_MCP_CONFIG (full path to the file)
      2. ~/.gmail-mcp/config.json
      3. <repo root>/config.json (legacy; prints a one-time stderr hint)
    """
    global _warned_legacy
    env = os.environ.get(CONFIG_ENV_VAR)
    if env:
        path = Path(env).expanduser()
        if not path.exists():
            raise ConfigError(
                f"{CONFIG_ENV_VAR} is set to {path}, but that file does not exist. "
                f"Create it, or unset {CONFIG_ENV_VAR} to use {DEFAULT_CONFIG_PATH}."
            )
        return path
    if DEFAULT_CONFIG_PATH.exists():
        return DEFAULT_CONFIG_PATH
    if LEGACY_CONFIG_PATH.exists():
        if not _warned_legacy:
            print(
                f"gmail-mcp: using legacy config at {LEGACY_CONFIG_PATH}. "
                f"Consider moving it to {DEFAULT_CONFIG_PATH}.",
                file=sys.stderr,
            )
            _warned_legacy = True
        return LEGACY_CONFIG_PATH
    raise ConfigError(
        f"No config.json found. Run `gmail-mcp-auth init` to create "
        f"{DEFAULT_CONFIG_PATH} from an example, then edit it to list your accounts "
        f"(or set {CONFIG_ENV_VAR} to the full path of a config file)."
    )


def write_example_config(path: Path = DEFAULT_CONFIG_PATH) -> bool:
    """Write the example config to `path` if it doesn't exist. Returns True if written."""
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(EXAMPLE_CONFIG, indent=2) + "\n")
    return True


def load_config() -> dict:
    path = find_config_path()
    try:
        with path.open() as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ConfigError(f"{path} is not valid JSON: {e}") from e
    if "accounts" not in data or not isinstance(data["accounts"], dict):
        raise ConfigError(
            f"{path} must have an 'accounts' object mapping aliases to emails."
        )
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
