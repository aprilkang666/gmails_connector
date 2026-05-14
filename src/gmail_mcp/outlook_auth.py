"""OAuth2 flow + per-account token storage for Microsoft 365 / Outlook.

Uses MSAL's public-client interactive flow (browser + loopback redirect),
the same shape as the Gmail flow in auth.py. The whole MSAL token cache is
stored as a JSON blob in the shared SQLite DB, keyed by email, with
provider='outlook'. MSAL handles silent refresh from the cached refresh
token.

A public client does not use a client secret — it relies on PKCE. Only
MICROSOFT_CLIENT_ID and MICROSOFT_TENANT_ID are needed here; any
MICROSOFT_CLIENT_SECRET in the environment is ignored by this flow.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import msal

# Delegated Microsoft Graph scopes. MSAL adds offline_access / openid /
# profile automatically — do not list them here.
SCOPES = [
    "Mail.ReadWrite",
    "Mail.Send",
    "MailboxSettings.ReadWrite",
    "User.Read",
]

DB_PATH = Path.home() / ".gmail-mcp" / "tokens.db"


class AuthError(Exception):
    pass


def _ensure_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS accounts (
            email TEXT PRIMARY KEY,
            token_json TEXT NOT NULL
        )
        """
    )
    # Idempotent migration shared with auth.py: ensure the provider column
    # exists so Gmail and Outlook rows can coexist in one table.
    cols = {row[1] for row in conn.execute("PRAGMA table_info(accounts)")}
    if "provider" not in cols:
        conn.execute(
            "ALTER TABLE accounts ADD COLUMN provider TEXT NOT NULL DEFAULT 'gmail'"
        )
    conn.commit()
    return conn


def _msal_app(
    token_cache: msal.SerializableTokenCache | None = None,
) -> msal.PublicClientApplication:
    """Build an MSAL public-client app from env vars."""
    client_id = os.environ.get("MICROSOFT_CLIENT_ID")
    tenant_id = os.environ.get("MICROSOFT_TENANT_ID")
    if not client_id or not tenant_id:
        raise AuthError(
            "MICROSOFT_CLIENT_ID and MICROSOFT_TENANT_ID must be set in your "
            "environment (or in .env.local / the MCP server's env block)."
        )
    return msal.PublicClientApplication(
        client_id,
        authority=f"https://login.microsoftonline.com/{tenant_id}",
        token_cache=token_cache,
    )


def authorize_account(email_hint: str | None = None) -> str:
    """Run the interactive OAuth flow to add a new Outlook account.

    Opens a browser, the user consents, and the MSAL token cache is saved.
    Returns the email address that actually consented. prompt='select_account'
    forces the account picker so the wrong mailbox can't be authorized by
    accident (same rationale as the Gmail flow).
    """
    cache = msal.SerializableTokenCache()
    app = _msal_app(cache)
    result = app.acquire_token_interactive(
        SCOPES,
        login_hint=email_hint,
        prompt="select_account",
    )
    if "access_token" not in result:
        raise AuthError(
            f"Microsoft sign-in failed: {result.get('error')}: "
            f"{result.get('error_description')}"
        )

    accounts = app.get_accounts()
    if not accounts:
        raise AuthError("Sign-in succeeded but MSAL returned no account.")
    actual_email = accounts[0]["username"]

    conn = _ensure_db()
    conn.execute(
        "INSERT OR REPLACE INTO accounts (email, token_json, provider) "
        "VALUES (?, ?, 'outlook')",
        (actual_email, cache.serialize()),
    )
    conn.commit()
    conn.close()
    return actual_email


def get_access_token(email: str) -> str:
    """Load the token cache for an account, silently refresh, return a token."""
    conn = _ensure_db()
    row = conn.execute(
        "SELECT token_json FROM accounts WHERE email = ? AND provider = 'outlook'",
        (email,),
    ).fetchone()
    conn.close()

    if not row:
        raise AuthError(
            f"No saved Outlook credentials for {email}. "
            f"Run: gmail-mcp-setup add <alias>"
        )

    cache = msal.SerializableTokenCache()
    cache.deserialize(row[0])
    app = _msal_app(cache)

    accounts = app.get_accounts(username=email)
    if not accounts:
        raise AuthError(
            f"No MSAL account entry for {email}. Re-run: gmail-mcp-setup add <alias>"
        )

    result = app.acquire_token_silent(SCOPES, account=accounts[0])
    if not result or "access_token" not in result:
        raise AuthError(
            f"Could not refresh the Outlook token for {email}. "
            f"Re-run: gmail-mcp-setup add <alias>"
        )

    # Persist the cache if MSAL rotated the refresh token.
    if cache.has_state_changed:
        conn = _ensure_db()
        conn.execute(
            "UPDATE accounts SET token_json = ? "
            "WHERE email = ? AND provider = 'outlook'",
            (cache.serialize(), email),
        )
        conn.commit()
        conn.close()

    return result["access_token"]


def list_authorized_emails() -> list[str]:
    conn = _ensure_db()
    rows = conn.execute(
        "SELECT email FROM accounts WHERE provider = 'outlook' ORDER BY email"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def remove_account(email: str) -> bool:
    conn = _ensure_db()
    cur = conn.execute(
        "DELETE FROM accounts WHERE email = ? AND provider = 'outlook'", (email,)
    )
    conn.commit()
    conn.close()
    return cur.rowcount > 0
