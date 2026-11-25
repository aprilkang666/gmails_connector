"""Stdio entry point for the Claude Desktop extension (.mcpb bundle).

Run with: python -m gmail_mcp.desktop

Claude Desktop passes every user_config field to the server as an env var,
including optional fields the user left blank. Depending on the host those
arrive as "" or as the literal "${user_config.X}" placeholder. This module
treats both as unset *before* gmail_mcp.config is imported (config reads
GMAIL_MCP_CONFIG at import time), then serves the normal stdio server.

The cleanup only runs here, never for the remote connector (remote.py) or
the plain stdio entry point (server.py).
"""
from __future__ import annotations

import os
from collections.abc import MutableMapping
from pathlib import Path

# Env vars the extension's manifest maps from user_config.
MANAGED_ENV_VARS = (
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "MICROSOFT_CLIENT_ID",
    "MICROSOFT_TENANT_ID",
    "GMAIL_MCP_CONFIG",
)


def clean_env(environ: MutableMapping[str, str]) -> None:
    """Drop blank or unsubstituted user_config values, and default the config path.

    The bundle's own directory is not a sensible place for config.json, so
    GMAIL_MCP_CONFIG defaults to <GMAIL_MCP_HOME or ~/.gmail-mcp>/config.json.
    """
    for name in MANAGED_ENV_VARS:
        value = environ.get(name)
        if value is not None and (not value.strip() or value.strip().startswith("${")):
            del environ[name]
    if "GMAIL_MCP_CONFIG" not in environ:
        home = Path(environ.get("GMAIL_MCP_HOME") or Path.home() / ".gmail-mcp")
        environ["GMAIL_MCP_CONFIG"] = str(home / "config.json")


def main() -> None:
    clean_env(os.environ)
    from .server import mcp  # imported after clean_env on purpose

    mcp.run()


if __name__ == "__main__":
    main()
