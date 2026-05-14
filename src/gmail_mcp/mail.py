"""Provider-agnostic mail dispatch.

Each per-account function looks up the alias's provider in config.json and
routes to the matching backend (gmail.py or outlook.py). server.py calls into
this module so its MCP tools work uniformly across Gmail and Microsoft 365
accounts.

Note on query syntax: search() and search_all() pass the query straight to the
backend. Gmail accounts expect Gmail query syntax; Outlook accounts expect
Microsoft Graph KQL syntax. A single query sent to search_all() will therefore
behave differently per provider.
"""
from __future__ import annotations

from . import auth, config, gmail, outlook, outlook_auth

_BACKENDS = {"gmail": gmail, "outlook": outlook}


def _backend(alias: str):
    provider = config.get_provider(alias)
    backend = _BACKENDS.get(provider)
    if backend is None:
        raise config.ConfigError(
            f"Account '{alias}' has unsupported provider '{provider}'."
        )
    return backend


# --- per-account operations (dispatch on provider) ---

def search(alias: str, query: str, max_results: int = 20) -> str:
    return _backend(alias).search(alias, query, max_results)


def read_message(alias: str, message_id: str) -> str:
    return _backend(alias).read_message(alias, message_id)


def read_thread(alias: str, thread_id: str) -> str:
    return _backend(alias).read_thread(alias, thread_id)


def create_draft(alias: str, to: str, subject: str, body: str,
                  cc: str = "", bcc: str = "") -> str:
    return _backend(alias).create_draft(alias, to, subject, body, cc, bcc)


def send_draft(alias: str, draft_id: str) -> str:
    return _backend(alias).send_draft(alias, draft_id)


def list_labels(alias: str) -> str:
    return _backend(alias).list_labels(alias)


def modify_labels(alias: str, message_id: str,
                  add: list[str] | None = None,
                  remove: list[str] | None = None) -> str:
    return _backend(alias).modify_labels(alias, message_id, add, remove)


# --- cross-account operations (provider-aware) ---

def _authorized_emails_by_provider() -> dict[str, set[str]]:
    return {
        "gmail": set(auth.list_authorized_emails()),
        "outlook": set(outlook_auth.list_authorized_emails()),
    }


def _is_authorized(account: dict, authorized: dict[str, set[str]]) -> bool:
    return account["email"] in authorized.get(account["provider"], set())


def list_all_accounts() -> str:
    """Return configured aliases with provider + authorized status."""
    aliases = config.load_config()["accounts"]
    authorized = _authorized_emails_by_provider()
    lines = ["Configured accounts:"]
    for alias in aliases:
        acct = config.resolve_account(alias)
        ok = _is_authorized(acct, authorized)
        status = "✅ authorized" if ok else "❌ not authorized (run setup_auth add)"
        lines.append(
            f"  {alias}: {acct['email']}  [{acct['provider']}]  [{status}]"
        )
    return "\n".join(lines)


def search_all(query: str, max_per_account: int = 10) -> str:
    """Fan-out search across every configured, authorized account."""
    aliases = config.load_config()["accounts"]
    authorized = _authorized_emails_by_provider()
    out = []
    for alias in aliases:
        acct = config.resolve_account(alias)
        label = f"{alias} ({acct['email']}) [{acct['provider']}]"
        if not _is_authorized(acct, authorized):
            out.append(f"=== {label}: skipped (not authorized) ===\n")
            continue
        out.append(f"=== {label} ===")
        try:
            out.append(search(alias, query, max_per_account))
        except Exception as e:
            out.append(f"  Error: {e}")
        out.append("")
    return "\n".join(out)
