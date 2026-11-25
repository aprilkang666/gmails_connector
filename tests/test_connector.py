"""Tests for the remote connector: OAuth gate, lockout, and inline attachments.

Run with: uv run --with pytest pytest -q
"""
from __future__ import annotations

import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlparse

import pytest
from starlette.testclient import TestClient

BASE = "http://localhost:8765"
CALLBACK = "https://claude.ai/api/mcp/auth_callback"
PASSWORD = "correct-horse-battery"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text('{"accounts": {"personal": "a@gmail.com"}}')
    monkeypatch.setenv("GMAIL_MCP_HOME", str(tmp_path))
    monkeypatch.setenv("GMAIL_MCP_CONFIG", str(cfg))
    monkeypatch.setenv("CONNECTOR_BASE_URL", BASE)
    monkeypatch.setenv("CONNECTOR_PASSWORD", PASSWORD)

    import importlib

    from gmail_mcp import config, remote
    importlib.reload(config)  # pick up the env-var paths
    mcp = remote.create_server()
    with TestClient(mcp.streamable_http_app(), base_url=BASE,
                    follow_redirects=False) as c:
        yield c


def _start_login(c: TestClient) -> tuple[str, str, str]:
    cid = c.post("/register", json={
        "redirect_uris": [CALLBACK], "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"], "client_name": "Claude",
    }).json()["client_id"]
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    r = c.get("/authorize", params={
        "response_type": "code", "client_id": cid, "redirect_uri": CALLBACK,
        "code_challenge": challenge, "code_challenge_method": "S256", "state": "s",
    })
    assert r.status_code == 302
    req = parse_qs(urlparse(r.headers["location"]).query)["req"][0]
    return cid, verifier, req


def test_mcp_requires_token(client):
    r = client.post("/mcp", json={})
    assert r.status_code == 401


def test_untrusted_redirect_host_rejected(client):
    for uri in ("https://evil.example/cb", "https://claude.ai.evil.example/cb",
                "http://claude.ai/cb"):
        assert client.post("/register", json={"redirect_uris": [uri]}).status_code == 400


def test_full_flow_issues_working_token(client):
    cid, verifier, req = _start_login(client)
    r = client.post("/connector/login", data={"req": req, "password": PASSWORD})
    assert r.status_code == 302 and r.headers["location"].startswith(CALLBACK)
    code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]
    tok = client.post("/token", data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": CALLBACK,
        "client_id": cid, "code_verifier": verifier,
    })
    assert tok.status_code == 200
    r = client.post("/mcp", headers={
        "Authorization": f"Bearer {tok.json()['access_token']}",
        "Accept": "application/json, text/event-stream",
    }, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "t", "version": "1"}}})
    assert r.status_code == 200


def test_lockout_after_repeated_wrong_passwords(client):
    _, _, req = _start_login(client)
    for _ in range(5):
        r = client.post("/connector/login", data={"req": req, "password": "nope-nope-nope"})
        assert r.status_code == 401
    # Even the right password is refused while locked out.
    r = client.post("/connector/login", data={"req": req, "password": PASSWORD})
    assert r.status_code == 429


def test_short_password_refused(tmp_path):
    from gmail_mcp.connector_auth import OwnerOAuthProvider
    with pytest.raises(ValueError):
        OwnerOAuthProvider(base_url=BASE, password="short",
                           db_path=tmp_path / "c.db")


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


@pytest.mark.parametrize("name,mime,data,expect", [
    ("a.png", "image/png", PNG, "image"),
    ("a.txt", "text/plain", b"hello", "hello"),
    ("a.xlsx", "application/vnd.ms-excel", b"\x00\x01", "can't be read"),
])
def test_remote_read_attachment_never_writes_to_disk(monkeypatch, name, mime, data, expect):
    import anyio

    from gmail_mcp import attachments, mail, server

    monkeypatch.setattr(mail, "_backend", lambda alias: type(
        "B", (), {"get_attachment": staticmethod(lambda *a: (name, mime, data))}))
    monkeypatch.setattr(attachments, "save_to_sandbox",
                        lambda *a: pytest.fail("remote mode must not save to disk"))
    mcp = server.build_server(remote=True)
    blocks = anyio.run(mcp.call_tool, "read_attachment",
                       {"account": "x", "message_id": "m", "attachment": "0"})
    blocks = blocks[0] if isinstance(blocks, tuple) else blocks
    if expect == "image":
        assert any(b.type == "image" and b.mimeType == "image/png" for b in blocks)
    else:
        assert expect in blocks[0].text
