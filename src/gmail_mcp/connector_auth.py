"""OAuth 2.1 authorization server for the remote (custom connector) mode.

claude.ai custom connectors speak OAuth to the MCP server: they register
themselves (dynamic client registration), send the user to /authorize, and
then call the MCP endpoint with a bearer token. The MCP SDK implements the
protocol endpoints (/register, /authorize, /token, /revoke and the metadata
documents) and validates PKCE, redirect URIs and code expiry. This module
supplies the storage and the one decision the SDK leaves to us: *who is
allowed to approve a connection*.

This server has exactly one owner — the person whose mailboxes it holds.
Approval is gated by an owner password (CONNECTOR_PASSWORD) entered on the
/connector/login page. Without it, nobody can obtain a token, even though
anyone on the internet can reach the URL and register a client.

Defences beyond the password:
  - Redirect URIs must point at an allowed host (claude.ai / claude.com by
    default), so a third party can't register a client that ships your
    authorization code to their own server and phish you into approving it.
  - Failed password attempts lock the login page for 15 minutes after 5
    failures (counted globally, since there is one owner).
  - Access and refresh tokens are random 256-bit values, stored only as
    SHA-256 hashes. Refresh tokens rotate on every use.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    RegistrationError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

ACCESS_TOKEN_TTL = 60 * 60                 # 1 hour
REFRESH_TOKEN_TTL = 60 * 60 * 24 * 30      # 30 days
AUTH_CODE_TTL = 5 * 60                     # 5 minutes
PENDING_LOGIN_TTL = 10 * 60                # time to type the password
MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 15 * 60
MIN_PASSWORD_LENGTH = 12

LOGIN_PATH = "/connector/login"

# Hosts claude.ai uses for its OAuth callback.
DEFAULT_REDIRECT_HOSTS = ("claude.ai", "claude.com")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass
class _PendingLogin:
    client_id: str
    client_name: str
    params: AuthorizationParams
    expires_at: float


class OwnerOAuthProvider:
    """OAuthAuthorizationServerProvider for a single, password-holding owner."""

    def __init__(
        self,
        *,
        base_url: str,
        password: str,
        db_path: Path,
        allowed_redirect_hosts: tuple[str, ...] = DEFAULT_REDIRECT_HOSTS,
    ) -> None:
        if len(password) < MIN_PASSWORD_LENGTH:
            raise ValueError(
                f"CONNECTOR_PASSWORD must be at least {MIN_PASSWORD_LENGTH} "
                "characters. Generate one with: "
                'python -c "import secrets; print(secrets.token_urlsafe(24))"'
            )
        self.base_url = base_url.rstrip("/")
        self._password = password.encode()
        self._allowed_hosts = tuple(h.lower() for h in allowed_redirect_hosts)
        self._db_path = db_path
        self._lock = threading.Lock()
        # Short-lived state is kept in memory; a restart just means the
        # owner has to click "Connect" again mid-flow.
        self._pending: dict[str, _PendingLogin] = {}
        self._codes: dict[str, AuthorizationCode] = {}
        self._failed_logins: list[float] = []
        self._init_db()

    # ------------------------------------------------------------------ db

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def _init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS clients (
                    client_id TEXT PRIMARY KEY,
                    info_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tokens (
                    token_hash TEXT PRIMARY KEY,
                    kind TEXT NOT NULL CHECK (kind IN ('access', 'refresh')),
                    client_id TEXT NOT NULL,
                    scopes TEXT NOT NULL,
                    expires_at INTEGER NOT NULL,
                    resource TEXT
                );
                """
            )
            conn.execute("DELETE FROM tokens WHERE expires_at < ?", (int(time.time()),))
        try:
            self._db_path.chmod(0o600)
        except OSError:
            pass

    def _store_token(self, token: str, kind: str, client_id: str,
                     scopes: list[str], ttl: int, resource: str | None) -> int:
        expires_at = int(time.time()) + ttl
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO tokens VALUES (?, ?, ?, ?, ?, ?)",
                (_hash(token), kind, client_id, json.dumps(scopes), expires_at, resource),
            )
        return expires_at

    def _load_token(self, token: str, kind: str) -> tuple | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT client_id, scopes, expires_at, resource FROM tokens "
                "WHERE token_hash = ? AND kind = ?",
                (_hash(token), kind),
            ).fetchone()
        if row is None or row[2] < time.time():
            return None
        return row

    def _delete_token(self, token: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM tokens WHERE token_hash = ?", (_hash(token),))

    def _issue_tokens(self, client_id: str, scopes: list[str],
                      resource: str | None) -> OAuthToken:
        access = secrets.token_urlsafe(32)
        refresh = secrets.token_urlsafe(32)
        self._store_token(access, "access", client_id, scopes, ACCESS_TOKEN_TTL, resource)
        self._store_token(refresh, "refresh", client_id, scopes, REFRESH_TOKEN_TTL, resource)
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=ACCESS_TOKEN_TTL,
            refresh_token=refresh,
            scope=" ".join(scopes) if scopes else None,
        )

    # ------------------------------------------------------ client registry

    def _redirect_allowed(self, uri: str) -> bool:
        parsed = urlparse(uri)
        host = (parsed.hostname or "").lower()
        if host in ("localhost", "127.0.0.1"):
            # Only for local testing, and only when explicitly allowed.
            return host in self._allowed_hosts and parsed.scheme in ("http", "https")
        return parsed.scheme == "https" and any(
            host == h or host.endswith("." + h) for h in self._allowed_hosts
        )

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT info_json FROM clients WHERE client_id = ?", (client_id,)
            ).fetchone()
        return OAuthClientInformationFull.model_validate_json(row[0]) if row else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        for uri in client_info.redirect_uris or []:
            if not self._redirect_allowed(str(uri)):
                raise RegistrationError(
                    error="invalid_redirect_uri",
                    error_description=(
                        f"Redirect URI {uri} is not allowed by this server. "
                        "Allowed hosts: " + ", ".join(self._allowed_hosts)
                    ),
                )
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO clients VALUES (?, ?)",
                (client_info.client_id, client_info.model_dump_json()),
            )

    # ---------------------------------------------------------- authorize

    async def authorize(self, client: OAuthClientInformationFull,
                        params: AuthorizationParams) -> str:
        """Park the request and send the browser to the owner login page."""
        now = time.time()
        with self._lock:
            for key in [k for k, p in self._pending.items() if p.expires_at < now]:
                del self._pending[key]
            request_id = secrets.token_urlsafe(32)
            self._pending[request_id] = _PendingLogin(
                client_id=client.client_id or "",
                client_name=client.client_name or "an MCP client",
                params=params,
                expires_at=now + PENDING_LOGIN_TTL,
            )
        return f"{self.base_url}{LOGIN_PATH}?req={request_id}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        with self._lock:
            code = self._codes.get(authorization_code)
        if code is None or code.client_id != client.client_id:
            return None
        return code

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        with self._lock:
            # Single use: remove before issuing so a replay finds nothing.
            self._codes.pop(authorization_code.code, None)
        return self._issue_tokens(
            client.client_id or "", authorization_code.scopes, authorization_code.resource
        )

    # ------------------------------------------------------------- tokens

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        row = self._load_token(refresh_token, "refresh")
        if row is None or row[0] != client.client_id:
            return None
        return RefreshToken(
            token=refresh_token, client_id=row[0],
            scopes=json.loads(row[1]), expires_at=row[2],
        )

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        row = self._load_token(refresh_token.token, "refresh")
        resource = row[3] if row else None
        self._delete_token(refresh_token.token)  # rotate
        return self._issue_tokens(
            client.client_id or "", scopes or refresh_token.scopes, resource
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        row = self._load_token(token, "access")
        if row is None:
            return None
        return AccessToken(
            token=token, client_id=row[0], scopes=json.loads(row[1]),
            expires_at=row[2], resource=row[3],
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        self._delete_token(token.token)

    # ------------------------------------------------------- login page

    def _locked_out(self) -> bool:
        cutoff = time.time() - LOCKOUT_SECONDS
        with self._lock:
            self._failed_logins = [t for t in self._failed_logins if t > cutoff]
            return len(self._failed_logins) >= MAX_FAILED_LOGINS

    def _take_pending(self, request_id: str, *, consume: bool) -> _PendingLogin | None:
        with self._lock:
            pending = self._pending.get(request_id)
            if pending is None or pending.expires_at < time.time():
                self._pending.pop(request_id, None)
                return None
            if consume:
                del self._pending[request_id]
            return pending

    async def handle_login(self, request: Request) -> Response:
        """GET shows the password form; POST checks it and redirects back."""
        if request.method == "GET":
            request_id = request.query_params.get("req", "")
            pending = self._take_pending(request_id, consume=False)
            if pending is None:
                return _page("This sign-in link has expired. Start the connection "
                             "again from Claude.", status=400)
            return _login_form(request_id, pending)

        form = await request.form()
        request_id = str(form.get("req", ""))
        password = str(form.get("password", ""))

        if self._locked_out():
            return _page("Too many failed attempts. Try again in 15 minutes.",
                         status=429)
        pending = self._take_pending(request_id, consume=False)
        if pending is None:
            return _page("This sign-in link has expired. Start the connection "
                         "again from Claude.", status=400)

        if not hmac.compare_digest(password.encode(), self._password):
            with self._lock:
                self._failed_logins.append(time.time())
            return _login_form(request_id, pending, error="Wrong password.", status=401)

        pending = self._take_pending(request_id, consume=True)
        if pending is None:  # raced with another submit
            return _page("This sign-in link was already used.", status=400)

        params = pending.params
        code = secrets.token_urlsafe(32)
        with self._lock:
            now = time.time()
            for key in [k for k, c in self._codes.items() if c.expires_at < now]:
                del self._codes[key]
            self._codes[code] = AuthorizationCode(
                code=code,
                scopes=params.scopes or [],
                expires_at=now + AUTH_CODE_TTL,
                client_id=pending.client_id,
                code_challenge=params.code_challenge,
                redirect_uri=params.redirect_uri,
                redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
                resource=params.resource,
            )
        target = construct_redirect_uri(str(params.redirect_uri), code=code,
                                        state=params.state)
        return RedirectResponse(target, status_code=302,
                                headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------- HTML

_SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
                               "form-action 'self'; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
}

_STYLE = """
body{font-family:system-ui,sans-serif;background:#f6f6f4;color:#1f1f1f;
     display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0;padding:16px}
main{background:#fff;border:1px solid #e3e3df;border-radius:12px;padding:28px;max-width:380px;width:100%}
h1{font-size:18px;margin:0 0 8px}p{font-size:14px;line-height:1.5;color:#555}
input{width:100%;box-sizing:border-box;padding:10px;font-size:15px;border:1px solid #ccc;border-radius:8px}
button{margin-top:12px;width:100%;padding:10px;font-size:15px;border:0;border-radius:8px;
       background:#1f1f1f;color:#fff;cursor:pointer}
.err{color:#b3261e;font-size:14px}
@media (prefers-color-scheme:dark){body{background:#1a1a19;color:#eee}
 main{background:#262624;border-color:#3a3a37}p{color:#bbb}
 input{background:#1a1a19;color:#eee;border-color:#555}button{background:#eee;color:#111}}
"""


def _page(message: str, status: int = 200) -> HTMLResponse:
    body = (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>Mail connector</title><style>{_STYLE}</style></head>"
            f"<body><main><h1>Mail connector</h1><p>{html.escape(message)}</p>"
            f"</main></body></html>")
    return HTMLResponse(body, status_code=status, headers=_SECURITY_HEADERS)


def _login_form(request_id: str, pending: _PendingLogin, *, error: str = "",
                status: int = 200) -> HTMLResponse:
    redirect_host = urlparse(str(pending.params.redirect_uri)).hostname or "?"
    err = f"<p class='err'>{html.escape(error)}</p>" if error else ""
    body = f"""<!doctype html><html><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Mail connector</title><style>{_STYLE}</style></head><body><main>
<h1>Connect your mailboxes</h1>
<p><b>{html.escape(pending.client_name)}</b> ({html.escape(redirect_host)}) wants to
read, draft and send mail in every account on this server.</p>
<p>Enter the connector password to allow it.</p>{err}
<form method='post' action='{LOGIN_PATH}'>
<input type='hidden' name='req' value='{html.escape(request_id)}'>
<input type='password' name='password' autocomplete='current-password'
       placeholder='Connector password' autofocus required>
<button type='submit'>Allow</button></form></main></body></html>"""
    return HTMLResponse(body, status_code=status, headers=_SECURITY_HEADERS)
