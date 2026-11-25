"""Remote entry point: serve the mail tools as a claude.ai custom connector.

Run with:  uv run python -m gmail_mcp.remote

The server speaks MCP over Streamable HTTP at <CONNECTOR_BASE_URL>/mcp and
protects it with OAuth (see connector_auth.py). It must be reachable from
the internet over HTTPS — put it behind a tunnel (Cloudflare Tunnel, ngrok,
Tailscale Funnel) or a hosting platform that terminates TLS.

Environment:
  CONNECTOR_BASE_URL   Public HTTPS origin, e.g. https://mail.example.com
                       (required; no trailing path)
  CONNECTOR_PASSWORD   Owner password typed when connecting from claude.ai
                       (required, >= 12 characters)
  HOST / PORT          Bind address (default 127.0.0.1:8000; use 0.0.0.0 in
                       a container)
  CONNECTOR_REDIRECT_HOSTS
                       Comma-separated OAuth callback hosts to accept
                       (default: claude.ai,claude.com)
  GMAIL_MCP_HOME       Directory for tokens.db and connector.db
                       (default ~/.gmail-mcp)
  GMAIL_MCP_CONFIG     Path to config.json (default: $GMAIL_MCP_HOME/config.json,
                       then the legacy repo-root config.json)
plus the GOOGLE_* / MICROSOFT_* variables the providers already need.
"""
from __future__ import annotations

import os
import sys
from urllib.parse import urlparse

from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from . import config
from .connector_auth import DEFAULT_REDIRECT_HOSTS, LOGIN_PATH, OwnerOAuthProvider
from .server import build_server


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(f"error: {name} must be set. See README → 'Use as a claude.ai custom connector'.")
    return value


def create_server():
    base_url = _require_env("CONNECTOR_BASE_URL").rstrip("/")
    parsed = urlparse(base_url)
    local = parsed.hostname in ("localhost", "127.0.0.1")
    if parsed.scheme != "https" and not local:
        sys.exit("error: CONNECTOR_BASE_URL must be an https:// URL "
                 "(claude.ai only connects over HTTPS).")
    if parsed.path not in ("", "/"):
        sys.exit("error: CONNECTOR_BASE_URL must be an origin with no path, "
                 "e.g. https://mail.example.com")

    redirect_hosts = tuple(
        h.strip() for h in os.environ.get("CONNECTOR_REDIRECT_HOSTS", "").split(",") if h.strip()
    ) or DEFAULT_REDIRECT_HOSTS

    try:
        provider = OwnerOAuthProvider(
            base_url=base_url,
            password=_require_env("CONNECTOR_PASSWORD"),
            db_path=config.DATA_DIR / "connector.db",
            allowed_redirect_hosts=redirect_hosts,
        )
    except ValueError as e:
        sys.exit(f"error: {e}")

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))

    # Only accept requests addressed to the public hostname (or localhost for
    # health checks), which blocks DNS-rebinding attacks on a local bind.
    public_host = parsed.netloc
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[public_host, "127.0.0.1:*", "localhost:*", "[::1]:*"],
        allowed_origins=[base_url, "https://claude.ai", "https://claude.com",
                         "http://127.0.0.1:*", "http://localhost:*"],
    )

    mcp = build_server(
        remote=True,
        host=host,
        port=port,
        auth_server_provider=provider,
        auth=AuthSettings(
            issuer_url=base_url,
            resource_server_url=f"{base_url}/mcp",
            client_registration_options=ClientRegistrationOptions(enabled=True),
            revocation_options=RevocationOptions(enabled=True),
        ),
        transport_security=security,
    )

    @mcp.custom_route(LOGIN_PATH, methods=["GET", "POST"], include_in_schema=False)
    async def login(request: Request) -> Response:
        return await provider.handle_login(request)

    @mcp.custom_route("/healthz", methods=["GET"], include_in_schema=False)
    async def healthz(request: Request) -> Response:
        return JSONResponse({"ok": True})

    return mcp


def main() -> None:
    mcp = create_server()
    print(f"Mail connector listening on {mcp.settings.host}:{mcp.settings.port}; "
          f"add {os.environ['CONNECTOR_BASE_URL'].rstrip('/')}/mcp as a custom "
          "connector in claude.ai.", file=sys.stderr)
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
