"""One-time setup CLI: add, list, or remove mail accounts.

Routes to the Gmail or Outlook (Microsoft 365) auth flow based on each
alias's provider in config.json.

Usage:
    gmail-mcp-setup init            create $GMAIL_MCP_HOME/config.json
                                    (default ~/.gmail-mcp) from an example
    gmail-mcp-setup add <alias>     authorize an account listed in config.json
    gmail-mcp-setup list            show configured and authorized accounts
    gmail-mcp-setup remove <alias>  delete stored credentials for an alias

`python -m gmail_mcp.setup_auth <command>` works the same way.
"""
from __future__ import annotations

import sys

from . import auth, config, outlook_auth

_AUTH_BACKENDS = {"gmail": auth, "outlook": outlook_auth}


def _backend(provider: str):
    backend = _AUTH_BACKENDS.get(provider)
    if backend is None:
        raise config.ConfigError(f"Unsupported provider '{provider}'.")
    return backend


def cmd_init() -> None:
    path = config.DEFAULT_CONFIG_PATH
    if config.write_example_config(path):
        print(f"Wrote example config to {path}")
        print("Edit it to list your accounts, then run `gmail-mcp-setup add <alias>`.")
    else:
        print(f"Config already exists at {path}; left it unchanged.")


def cmd_add(alias: str) -> None:
    acct = config.resolve_account(alias)  # fails fast if alias unknown
    email_hint, provider = acct["email"], acct["provider"]
    backend = _backend(provider)
    print(
        f"Opening browser to authorize {email_hint} "
        f"(alias: {alias}, provider: {provider})..."
    )
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
    print(f"Accounts in {config.find_config_path()}:")
    for alias in config.list_account_aliases():
        acct = config.resolve_account(alias)
        print(f"  {alias}: {acct['email']}  [{acct['provider']}]")
    print("\nAuthorized (tokens stored):")
    for email in auth.list_authorized_emails():
        print(f"  {email}  [gmail]")
    for email in outlook_auth.list_authorized_emails():
        print(f"  {email}  [outlook]")


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
    try:
        if cmd == "init" and not rest:
            cmd_init()
        elif cmd == "add" and len(rest) == 1:
            cmd_add(rest[0])
        elif cmd == "list" and not rest:
            cmd_list()
        elif cmd == "remove" and len(rest) == 1:
            cmd_remove(rest[0])
        else:
            print(__doc__)
            sys.exit(1)
    except config.ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
