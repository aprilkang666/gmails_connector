"""OAuth2 flow + per-account token storage in SQLite.

Each account gets a row keyed by email address. Tokens are refreshed
automatically on read if they've expired.
"""
from __future__ import annotations

import json
import os
import sqlite3

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from . import config


# Minimum scopes for our toolset. Adjust if you add/remove tools.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",    # create drafts
    "https://www.googleapis.com/auth/gmail.send",       # send drafts
    "https://www.googleapis.com/auth/gmail.modify",     # labels, archive, mark read
]

DB_PATH = config.DATA_DIR / "tokens.db"


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
    # Idempotent migration: the 'provider' column was added when Outlook
    # support landed. Existing rows predate it and are all Gmail.
    cols = {row[1] for row in conn.execute("PRAGMA table_info(accounts)")}
    if "provider" not in cols:
        conn.execute(
            "ALTER TABLE accounts ADD COLUMN provider TEXT NOT NULL DEFAULT 'gmail'"
        )
    conn.commit()
    return conn


def _client_config() -> dict:
    """Build the OAuth client config dict from env vars.

    Expects GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to be set.
    """
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise AuthError(
            "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set in your environment "
            "(or in the MCP server's env block in Claude Code config)."
        )
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }


def authorize_account(email_hint: str | None = None) -> str:
    """Run the interactive OAuth flow to add a new account.

    Opens a browser, user consents, token is saved. Returns the email address
    that actually consented (pulled from the ID token / userinfo).
    """
    flow = InstalledAppFlow.from_client_config(_client_config(), SCOPES)
    # port=0 picks a free port automatically.
    # prompt='select_account' forces Google to show the account picker even
    # when a single account is already signed in, so users can't accidentally
    # authorize the wrong Gmail. login_hint pre-selects the expected account.
    creds = flow.run_local_server(
        port=0, login_hint=email_hint, prompt="select_account"
    )

    # Figure out which email consented by calling the Gmail API
    from googleapiclient.discovery import build
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    profile = service.users().getProfile(userId="me").execute()
    actual_email = profile["emailAddress"]

    conn = _ensure_db()
    conn.execute(
        "INSERT OR REPLACE INTO accounts (email, token_json, provider) "
        "VALUES (?, ?, 'gmail')",
        (actual_email, creds.to_json()),
    )
    conn.commit()
    conn.close()
    return actual_email


def get_credentials(email: str) -> Credentials:
    """Load credentials for an account, refreshing if expired."""
    conn = _ensure_db()
    row = conn.execute(
        "SELECT token_json FROM accounts WHERE email = ?", (email,)
    ).fetchone()
    conn.close()

    if not row:
        raise AuthError(
            f"No saved credentials for {email}. "
            f"Run: gmail-mcp-setup add <alias>"
        )

    creds = Credentials.from_authorized_user_info(json.loads(row[0]), SCOPES)

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        # Persist the refreshed token
        conn = _ensure_db()
        conn.execute(
            "UPDATE accounts SET token_json = ? WHERE email = ?",
            (creds.to_json(), email),
        )
        conn.commit()
        conn.close()

    if not creds.valid:
        raise AuthError(f"Credentials for {email} are invalid. Re-run `gmail-mcp-setup add`.")

    return creds


def list_authorized_emails() -> list[str]:
    conn = _ensure_db()
    rows = conn.execute(
        "SELECT email FROM accounts WHERE provider = 'gmail' ORDER BY email"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def remove_account(email: str) -> bool:
    conn = _ensure_db()
    cur = conn.execute(
        "DELETE FROM accounts WHERE email = ? AND provider = 'gmail'", (email,)
    )
    conn.commit()
    conn.close()
    return cur.rowcount > 0
