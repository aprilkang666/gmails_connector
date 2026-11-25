"""build_server() registers the expected tools in both local (stdio) and
remote (claude.ai connector) modes, and read_attachment routes correctly."""

from __future__ import annotations

import asyncio

import pytest

from gmail_mcp import mail, server

EXPECTED_TOOLS = {
    "list_accounts",
    "search_emails",
    "search_all_accounts",
    "read_email",
    "read_thread",
    "create_draft",
    "send_draft",
    "list_labels",
    "modify_labels",
    "list_attachments",
    "read_attachment",
}
UNSCOPED_TOOLS = {"list_accounts", "search_all_accounts"}

MODES = pytest.mark.parametrize("remote", [False, True], ids=["local", "remote"])


def _tools(mcp):
    return asyncio.run(mcp.list_tools())


def test_module_level_stdio_server():
    assert server.mcp.name == "gmail-mcp"
    names = {t.name for t in _tools(server.mcp)}
    assert names >= EXPECTED_TOOLS


@MODES
def test_expected_tools_registered(remote):
    names = {t.name for t in _tools(server.build_server(remote=remote))}
    # Subset check: new tools may be added without breaking this test.
    assert names >= EXPECTED_TOOLS, f"missing: {EXPECTED_TOOLS - names}"


@MODES
def test_every_tool_has_a_description(remote):
    for tool in _tools(server.build_server(remote=remote)):
        assert tool.description and tool.description.strip(), tool.name


@MODES
def test_account_scoped_tools_require_account(remote):
    for tool in _tools(server.build_server(remote=remote)):
        if tool.name in EXPECTED_TOOLS - UNSCOPED_TOOLS:
            assert "account" in tool.inputSchema.get("required", []), tool.name


def test_build_server_returns_independent_instances():
    a, b = server.build_server(), server.build_server(remote=True)
    assert a is not b and a is not server.mcp


def _call(mcp, name, args):
    return asyncio.run(mcp.call_tool(name, args))


def _texts(result):
    # FastMCP.call_tool returns either a content list or (content, structured).
    content = result[0] if isinstance(result, tuple) else result
    return [getattr(c, "text", None) for c in content]


ARGS = {"account": "personal", "message_id": "m1", "attachment": "0"}


def test_local_read_attachment_uses_sandbox_path(monkeypatch):
    calls = []
    monkeypatch.setattr(mail, "read_attachment", lambda *a: calls.append(a) or "LOCAL")
    monkeypatch.setattr(
        mail, "read_attachment_inline", lambda *a: pytest.fail("remote path used locally")
    )
    out = _call(server.build_server(remote=False), "read_attachment", ARGS)
    assert calls == [("personal", "m1", "0")]
    assert "LOCAL" in _texts(out)


def test_remote_read_attachment_never_writes_to_disk(monkeypatch):
    monkeypatch.setattr(
        mail, "read_attachment", lambda *a: pytest.fail("local sandbox path used remotely")
    )
    monkeypatch.setattr(mail, "read_attachment_inline", lambda *a: "REMOTE TEXT")
    out = _call(server.build_server(remote=True), "read_attachment", ARGS)
    assert "REMOTE TEXT" in _texts(out)


def test_remote_read_attachment_returns_image(monkeypatch):
    png = b"\x89PNG\r\n\x1a\n"
    monkeypatch.setattr(
        mail, "read_attachment_inline", lambda *a: ("🖼️ logo.png", png, "png")
    )
    out = _call(server.build_server(remote=True), "read_attachment", ARGS)
    content = out[0] if isinstance(out, tuple) else out
    kinds = [c.type for c in content]
    assert kinds == ["text", "image"]
    assert content[1].mimeType == "image/png"
