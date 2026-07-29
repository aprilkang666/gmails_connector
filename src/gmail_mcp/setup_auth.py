"""One-time setup CLI: add, list, or remove mail accounts.

Routes to the Gmail or Outlook (Microsoft 365) auth flow based on each
alias's provider in config.json.

Usage:
    uv run python -m gmail_mcp.setup_auth add <alias>
    uv run python -m gmail_mcp.setup_auth list
    uv run python -m gmail_mcp.setup_auth remove <alias>
"""
from __future__ import annotations

import sys

from . import auth, config, imap_auth, outlook_auth

_AUTH_BACKENDS = {"gmail": auth, "outlook": outlook_auth, "imap": imap_auth}


def _backend(provider: str):
    backend = _AUTH_BACKENDS.get(provider)
    if backend is None:
        raise config.ConfigError(f"Unsupported provider '{provider}'.")
    return backend


def cmd_add(alias: str) -> None:
    acct = config.resolve_account(alias)  # fails fast if alias unknown
    email_hint, provider = acct["email"], acct["provider"]
    backend = _backend(provider)
    print(f"Authorizing {email_hint} (alias: {alias}, provider: {provider})...")
    if provider == "imap":
        print("Paste the app password for this account when prompted.")
    else:
        print("Sign in with the matching account when the browser opens.")
    actual = backend.authorize_account(email_hint=email_hint)
    if actual.lower() != email_hint.lower():
        print(
            f"⚠️  You signed in as {actual}, but config.json lists {email_hint} "
            f"for alias '{alias}'."
        )
        print("Either re-run and pick the right account, or update config.json.")
    else:
        print(f"✅ Saved credentials for {actual}")


def cmd_list() -> None:
    print("Accounts in config.json:")
    for alias in config.list_account_aliases():
        acct = config.resolve_account(alias)
        print(f"  {alias}: {acct['email']}  [{acct['provider']}]")
    print("\nAuthorized (tokens stored):")
    for email in auth.list_authorized_emails():
        print(f"  {email}  [gmail]")
    for email in outlook_auth.list_authorized_emails():
        print(f"  {email}  [outlook]")
    for email in imap_auth.list_authorized_emails():
        print(f"  {email}  [imap]")


def cmd_remove(alias: str) -> None:
    acct = config.resolve_account(alias)
    backend = _backend(acct["provider"])
    removed = backend.remove_account(acct["email"])
    verb = "Removed" if removed else "No saved credentials for"
    print(f"{verb} {acct['email']}")


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
