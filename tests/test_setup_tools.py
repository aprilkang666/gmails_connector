"""The local setup tools must never be exposed by the remote connector.

authorize_account opens a browser and runs an OAuth loopback flow on the
machine running the server; setup_status reveals local file paths. Both are
for the local stdio server (Claude Code / Claude Desktop) only.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from gmail_mcp import desktop
from gmail_mcp.server import build_server

LOCAL_ONLY = {"setup_status", "authorize_account"}


def _tool_names(mcp) -> set[str]:
    return {t.name for t in asyncio.run(mcp.list_tools())}


def test_remote_server_has_no_setup_tools():
    assert not (_tool_names(build_server(remote=True)) & LOCAL_ONLY)


def test_remote_connector_has_no_setup_tools(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text('{"accounts": {"personal": "a@gmail.com"}}')
    monkeypatch.setenv("GMAIL_MCP_HOME", str(tmp_path))
    monkeypatch.setenv("GMAIL_MCP_CONFIG", str(cfg))
    monkeypatch.setenv("CONNECTOR_BASE_URL", "http://localhost:8765")
    monkeypatch.setenv("CONNECTOR_PASSWORD", "correct-horse-battery")

    import importlib

    from gmail_mcp import config, remote
    importlib.reload(config)  # pick up the env-var paths (keeps tests out of ~/.gmail-mcp)
    names = _tool_names(remote.create_server())
    assert "list_accounts" in names
    assert not (names & LOCAL_ONLY)


def test_local_server_has_setup_tools():
    assert LOCAL_ONLY <= _tool_names(build_server(remote=False))


@pytest.mark.parametrize("value", ["", "  ", "${user_config.google_client_id}"])
def test_desktop_clean_env_drops_blank_and_placeholder(value):
    env = {"GOOGLE_CLIENT_ID": value, "GMAIL_MCP_HOME": "/data"}
    desktop.clean_env(env)
    assert "GOOGLE_CLIENT_ID" not in env
    assert env["GMAIL_MCP_CONFIG"] == os.path.join("/data", "config.json")


def test_desktop_clean_env_keeps_real_values():
    env = {"GOOGLE_CLIENT_ID": "abc.apps.googleusercontent.com",
           "GMAIL_MCP_CONFIG": "/x/config.json",
           "UNRELATED": ""}
    desktop.clean_env(env)
    assert env == {"GOOGLE_CLIENT_ID": "abc.apps.googleusercontent.com",
                   "GMAIL_MCP_CONFIG": "/x/config.json",
                   "UNRELATED": ""}


def test_server_and_remote_import_do_not_touch_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "${user_config.google_client_id}")
    import importlib

    from gmail_mcp import remote, server
    importlib.reload(server)
    importlib.reload(remote)
    assert os.environ["GOOGLE_CLIENT_ID"] == "${user_config.google_client_id}"
