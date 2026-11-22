"""The MCP server module imports cleanly and registers the expected tools."""

from __future__ import annotations

import asyncio

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


def _tools():
    from gmail_mcp import server

    return asyncio.run(server.mcp.list_tools())


def test_server_imports():
    from gmail_mcp import server

    assert server.mcp.name == "gmail-mcp"


def test_expected_tools_registered():
    names = {t.name for t in _tools()}
    # Subset check: new tools may be added without breaking this test.
    assert names >= EXPECTED_TOOLS, f"missing: {EXPECTED_TOOLS - names}"


def test_every_tool_has_a_description():
    for tool in _tools():
        assert tool.description and tool.description.strip(), tool.name


def test_account_scoped_tools_require_account():
    for tool in _tools():
        if tool.name in EXPECTED_TOOLS - {"list_accounts", "search_all_accounts"}:
            assert "account" in tool.inputSchema.get("required", []), tool.name
