"""Token storage in SQLite: Gmail (auth.py) and Outlook (outlook_auth.py)
share one table distinguished by the 'provider' column. DB_PATH is redirected
to tmp_path by the autouse fixture in conftest.py."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from gmail_mcp import auth, outlook_auth


def _utcnow() -> datetime:
    # google-auth compares against naive UTC datetimes.
    return datetime.now(UTC).replace(tzinfo=None)


def _rows(db):
    with sqlite3.connect(db) as c:
        return c.execute("SELECT email, provider FROM accounts ORDER BY email").fetchall()


def _insert(db, email, token, provider):
    with sqlite3.connect(db) as c:
        c.execute(
            "INSERT INTO accounts (email, token_json, provider) VALUES (?, ?, ?)",
            (email, token, provider),
        )


def test_db_path_is_isolated(isolated_token_db):
    assert auth.DB_PATH == isolated_token_db
    assert outlook_auth.DB_PATH == isolated_token_db


def test_ensure_db_creates_schema(isolated_token_db):
    auth._ensure_db().close()
    assert isolated_token_db.exists()
    with sqlite3.connect(isolated_token_db) as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(accounts)")}
    assert cols == {"email", "token_json", "provider"}


@pytest.mark.parametrize("module", [auth, outlook_auth])
def test_migration_adds_provider_column_to_legacy_db(isolated_token_db, module):
    isolated_token_db.parent.mkdir(parents=True)
    with sqlite3.connect(isolated_token_db) as c:
        c.execute("CREATE TABLE accounts (email TEXT PRIMARY KEY, token_json TEXT NOT NULL)")
        c.execute("INSERT INTO accounts VALUES ('old@gmail.com', '{}')")

    module._ensure_db().close()
    module._ensure_db().close()  # idempotent

    assert _rows(isolated_token_db) == [("old@gmail.com", "gmail")]


def test_list_and_remove_are_provider_scoped(isolated_token_db):
    auth._ensure_db().close()
    _insert(isolated_token_db, "b@gmail.com", "{}", "gmail")
    _insert(isolated_token_db, "a@gmail.com", "{}", "gmail")
    _insert(isolated_token_db, "me@work.com", "{}", "outlook")

    assert auth.list_authorized_emails() == ["a@gmail.com", "b@gmail.com"]
    assert outlook_auth.list_authorized_emails() == ["me@work.com"]

    # Cross-provider removal is a no-op.
    assert auth.remove_account("me@work.com") is False
    assert outlook_auth.remove_account("a@gmail.com") is False
    assert auth.remove_account("a@gmail.com") is True
    assert outlook_auth.remove_account("me@work.com") is True
    assert _rows(isolated_token_db) == [("b@gmail.com", "gmail")]


def _google_token(expiry: datetime) -> str:
    return json.dumps(
        {
            "token": "ACCESS",
            "refresh_token": "REFRESH",
            "client_id": "cid",
            "client_secret": "csecret",
            "token_uri": "https://oauth2.googleapis.com/token",
            "scopes": auth.SCOPES,
            "expiry": expiry.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        }
    )


def test_get_credentials_valid_token(isolated_token_db):
    auth._ensure_db().close()
    _insert(isolated_token_db, "me@gmail.com", _google_token(_utcnow() + timedelta(hours=1)), "gmail")
    creds = auth.get_credentials("me@gmail.com")
    assert creds.token == "ACCESS"
    assert creds.valid


def test_get_credentials_refreshes_and_persists(isolated_token_db, monkeypatch):
    auth._ensure_db().close()
    _insert(isolated_token_db, "me@gmail.com", _google_token(_utcnow() - timedelta(hours=1)), "gmail")

    def fake_refresh(self, request):
        self.token = "NEW-ACCESS"
        self.expiry = _utcnow() + timedelta(hours=1)

    monkeypatch.setattr(auth.Credentials, "refresh", fake_refresh)
    creds = auth.get_credentials("me@gmail.com")
    assert creds.token == "NEW-ACCESS"

    with sqlite3.connect(isolated_token_db) as c:
        stored = json.loads(c.execute("SELECT token_json FROM accounts").fetchone()[0])
    assert stored["token"] == "NEW-ACCESS"


def test_get_credentials_missing_account():
    with pytest.raises(auth.AuthError, match="No saved credentials for nobody@gmail.com"):
        auth.get_credentials("nobody@gmail.com")


def test_outlook_get_access_token_missing_account():
    with pytest.raises(outlook_auth.AuthError, match="No saved Outlook credentials"):
        outlook_auth.get_access_token("nobody@work.com")


def test_outlook_token_not_served_for_gmail_row(isolated_token_db):
    """A Gmail row with the same email must not be read as an MSAL cache."""
    auth._ensure_db().close()
    _insert(isolated_token_db, "dual@x.com", "{}", "gmail")
    with pytest.raises(outlook_auth.AuthError, match="No saved Outlook credentials"):
        outlook_auth.get_access_token("dual@x.com")


def test_client_config_requires_env(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    with pytest.raises(auth.AuthError, match="GOOGLE_CLIENT_ID"):
        auth._client_config()
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    assert auth._client_config()["installed"]["client_id"] == "id"


def test_msal_app_requires_env(monkeypatch):
    monkeypatch.delenv("MICROSOFT_CLIENT_ID", raising=False)
    monkeypatch.delenv("MICROSOFT_TENANT_ID", raising=False)
    with pytest.raises(outlook_auth.AuthError, match="MICROSOFT_CLIENT_ID"):
        outlook_auth._msal_app()
