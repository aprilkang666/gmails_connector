"""Credential storage for IMAP/SMTP accounts (Gmail app passwords, Alibaba, etc.).

Stores email + password in the shared SQLite DB with provider='imap'. No OAuth —
these providers authenticate with a password over TLS.

The password is stored in plaintext in ~/.gmail-mcp/tokens.db, protected only by
file permissions. Use a provider-issued app password rather than your account
password, so it can be revoked without changing your login.
"""
from __future__ import annotations

import getpass
import sqlite3
from pathlib import Path

DB_PATH = Path.home() / ".gmail-mcp" / "tokens.db"


class AuthError(Exception):
    pass


def _ensure_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    existed = DB_PATH.exists()
    conn = sqlite3.connect(DB_PATH)
    if not existed:
        DB_PATH.chmod(0o600)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS accounts (
            email TEXT PRIMARY KEY,
            token_json TEXT NOT NULL
        )
        """
    )
    cols = {row[1] for row in conn.execute("PRAGMA table_info(accounts)")}
    if "provider" not in cols:
        conn.execute(
            "ALTER TABLE accounts ADD COLUMN provider TEXT NOT NULL DEFAULT 'gmail'"
        )
    conn.commit()
    return conn


def authorize_account(email_hint: str | None = None) -> str:
    """Prompt for an app password and store it."""
    email = email_hint or input("Email address: ").strip()
    password = getpass.getpass(f"App password for {email} (input hidden): ")
    # Gmail shows app passwords in four space-separated groups; IMAP login
    # rejects the spaces, so accept a pasted value either way.
    password = "".join(password.split())
    if not password:
        raise AuthError("Password cannot be empty.")

    conn = _ensure_db()
    conn.execute(
        "INSERT OR REPLACE INTO accounts (email, token_json, provider) "
        "VALUES (?, ?, 'imap')",
        (email, password),
    )
    conn.commit()
    conn.close()
    return email


def get_password(email: str) -> str:
    conn = _ensure_db()
    row = conn.execute(
        "SELECT token_json FROM accounts WHERE email = ? AND provider = 'imap'",
        (email,),
    ).fetchone()
    conn.close()
    if not row:
        raise AuthError(
            f"No saved IMAP credentials for {email}. "
            f"Run: gmail-mcp-setup add <alias>"
        )
    return row[0]


def list_authorized_emails() -> list[str]:
    conn = _ensure_db()
    rows = conn.execute(
        "SELECT email FROM accounts WHERE provider = 'imap' ORDER BY email"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def remove_account(email: str) -> bool:
    conn = _ensure_db()
    cur = conn.execute(
        "DELETE FROM accounts WHERE email = ? AND provider = 'imap'", (email,)
    )
    conn.commit()
    conn.close()
    return cur.rowcount > 0
