"""One-time setup CLI: add, list, or remove Gmail accounts.

Usage:
    uv run python -m gmail_mcp.setup_auth add <alias>
    uv run python -m gmail_mcp.setup_auth list
    uv run python -m gmail_mcp.setup_auth remove <alias>
"""
from __future__ import annotations

import sys

from . import auth, config


def cmd_add(alias: str) -> None:
    email_hint = config.resolve_alias(alias)  # fails fast if alias unknown
    print(f"Opening browser to authorize {email_hint} (alias: {alias})...")
    print("Sign in with the matching Google account when the browser opens.")
    actual = auth.authorize_account(email_hint=email_hint)
    if actual.lower() != email_hint.lower():
        print(
            f"⚠️  You signed in as {actual}, but config.json lists {email_hint} for alias '{alias}'."
        )
        print("Either re-run and pick the right account, or update config.json.")
    else:
        print(f"✅ Saved credentials for {actual}")


def cmd_list() -> None:
    print("Accounts in config.json:")
    for alias, email in config.load_config()["accounts"].items():
        print(f"  {alias}: {email}")
    print("\nAuthorized (tokens stored):")
    for email in auth.list_authorized_emails():
        print(f"  {email}")


def cmd_remove(alias: str) -> None:
    email = config.resolve_alias(alias)
    removed = auth.remove_account(email)
    print(f"{'Removed' if removed else 'No saved credentials for'} {email}")


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    cmd, *rest = args
    if cmd == "add" and len(rest) == 1:
        cmd_add(rest[0])
    elif cmd == "list":
        cmd_list()
    elif cmd == "remove" and len(rest) == 1:
        cmd_remove(rest[0])
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
