"""Shared fixtures. Nothing here touches the network or the real home dir."""

from __future__ import annotations

import base64

import pytest

from gmail_mcp import auth, config, outlook_auth

ACCOUNTS = {
    "personal": "me@gmail.com",
    "work": {"email": "me@work.com", "provider": "outlook"},
    "side": {"email": "side@gmail.com", "provider": "gmail"},
}


@pytest.fixture
def fake_config(monkeypatch):
    """Replace load_config with an in-memory config. Returns the accounts dict
    so tests can mutate it before calling into the code under test."""
    accounts = dict(ACCOUNTS)
    monkeypatch.setattr(config, "load_config", lambda: {"accounts": accounts})
    return accounts


@pytest.fixture(autouse=True)
def isolated_token_db(tmp_path, monkeypatch):
    """Point both auth modules at a throwaway SQLite file so no test can ever
    read or write ~/.gmail-mcp/tokens.db."""
    db = tmp_path / "gmail-mcp" / "tokens.db"
    monkeypatch.setattr(auth, "DB_PATH", db)
    monkeypatch.setattr(outlook_auth, "DB_PATH", db)
    return db


@pytest.fixture(autouse=True)
def isolated_sandbox(tmp_path, monkeypatch):
    """Redirect attachment saves away from ~/Downloads."""
    from gmail_mcp import attachments

    sandbox = tmp_path / "Downloads" / "gmail-mcp"
    monkeypatch.setattr(attachments, "SANDBOX", sandbox)
    return sandbox


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Fail loudly if any test forgets to mock httpx."""
    import httpx

    def _blocked(*args, **kwargs):
        raise AssertionError("Real network call attempted in tests")

    monkeypatch.setattr(httpx, "request", _blocked)


def b64url(text: str | bytes) -> str:
    if isinstance(text, str):
        text = text.encode()
    return base64.urlsafe_b64encode(text).decode()
