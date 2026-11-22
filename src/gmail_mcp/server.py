"""MCP server entry point. Run with: uv run python -m gmail_mcp.server

Exposes Gmail and Microsoft 365 / Outlook tools to Claude Code. Every tool
takes an `account` alias as defined in config.json (except `list_accounts`
and `search_all_accounts`); the mail module dispatches to the right backend
based on each account's provider.
"""
from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path

import anyio
from mcp.server.fastmcp import FastMCP

from . import auth, config, mail, outlook_auth, setup_auth

# Env vars the server reads. When launched as a Claude Desktop Extension,
# optional user_config fields the user left blank may arrive as an empty
# string (or, depending on the host, as the literal "${user_config.X}"
# placeholder). Treat both as unset so downstream code sees a clean env.
_MANAGED_ENV_VARS = (
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "MICROSOFT_CLIENT_ID",
    "MICROSOFT_TENANT_ID",
    "GMAIL_MCP_CONFIG",
)
for _name in _MANAGED_ENV_VARS:
    _val = os.environ.get(_name)
    if _val is not None and (not _val.strip() or _val.strip().startswith("${")):
        del os.environ[_name]

mcp = FastMCP("gmail-mcp")


@mcp.tool()
def list_accounts() -> str:
    """List all configured mail accounts, their provider, and auth status.

    Always call this first to see what account aliases are available and
    whether each is a Gmail or Outlook (Microsoft 365) account.
    """
    return mail.list_all_accounts()


@mcp.tool()
def search_emails(account: str, query: str, max_results: int = 20) -> str:
    """Search one mail account. Query syntax depends on the account's provider.

    Gmail accounts use Gmail query syntax:
      - 'from:boss@example.com is:unread'
      - 'subject:"quarterly review" after:2026/01/01'
      - 'has:attachment filename:pdf'

    Outlook (Microsoft 365) accounts use Microsoft Graph KQL syntax:
      - 'from:boss@example.com'
      - 'subject:invoice'
      - 'received:2026-01-01..2026-03-01'
    Check the account's provider with list_accounts first if unsure.

    Args:
        account: Account alias (e.g. 'personal', 'work') — see list_accounts.
        query: Search query in the provider's syntax.
        max_results: Cap on messages returned (default 20).

    Returns a formatted list of matching messages with IDs, senders, subjects, snippets.
    Use the message IDs with read_email.
    """
    return mail.search(account, query, max_results)


@mcp.tool()
def search_all_accounts(query: str, max_per_account: int = 10) -> str:
    """Run the same search across every authorized account.

    Useful for 'do I have any unread emails from X' across personal + work.
    """
    return mail.search_all(query, max_per_account)


@mcp.tool()
def read_email(account: str, message_id: str) -> str:
    """Read the full body of a single message by its ID.

    Args:
        account: Account alias.
        message_id: Message ID returned by search_emails.
    """
    return mail.read_message(account, message_id)


@mcp.tool()
def read_thread(account: str, thread_id: str) -> str:
    """Read an entire email thread (parent + all replies) by its thread ID.

    Note: a message's thread_id is often the same as its message_id for
    single-message threads. For multi-message threads, search results include both.
    """
    return mail.read_thread(account, thread_id)


@mcp.tool()
def create_draft(account: str, to: str, subject: str, body: str,
                 cc: str = "", bcc: str = "") -> str:
    """Create a draft email in the specified account. Does NOT send.

    Always create a draft first. The user reviews it, then calls send_draft
    explicitly.

    Args:
        account: Account alias to send FROM.
        to: Recipient email(s), comma-separated.
        subject: Email subject.
        body: Plain text body. Use \\n for line breaks.
        cc: Optional CC recipients.
        bcc: Optional BCC recipients.
    """
    return mail.create_draft(account, to, subject, body, cc, bcc)


@mcp.tool()
def send_draft(account: str, draft_id: str) -> str:
    """Send a previously created draft. This ACTUALLY sends the email.

    Only call after the user has confirmed they want to send the draft.

    Args:
        account: Same account the draft was created in.
        draft_id: ID returned by create_draft.
    """
    return mail.send_draft(account, draft_id)


@mcp.tool()
def list_labels(account: str) -> str:
    """List all labels (folders) in an account. Returns label IDs and names.

    Use the label IDs with modify_labels.
    """
    return mail.list_labels(account)


@mcp.tool()
def modify_labels(account: str, message_id: str,
                  add: list[str] | None = None,
                  remove: list[str] | None = None) -> str:
    """Add or remove labels on a message.

    Common operations:
      - Archive: remove=['INBOX']
      - Mark as read: remove=['UNREAD']
      - Mark as unread: add=['UNREAD']
      - Star: add=['STARRED']
      - Move to Trash: add=['TRASH']

    Args:
        account: Account alias.
        message_id: Target message.
        add: Label IDs to add.
        remove: Label IDs to remove.
    """
    return mail.modify_labels(account, message_id, add, remove)


@mcp.tool()
def list_attachments(account: str, message_id: str) -> str:
    """List the attachments on a message: index, filename, type, size.

    Use the index (e.g. '0') or the filename with read_attachment.

    Args:
        account: Account alias.
        message_id: Message ID returned by search_emails.
    """
    return mail.list_attachments(account, message_id)


@mcp.tool()
def read_attachment(account: str, message_id: str, attachment: str) -> str:
    """Read one attachment from a message.

    PDFs are returned as extracted text. Plain-text and CSV files are
    returned inline. Other binary files (images, spreadsheets) are saved to
    ~/Downloads/gmail-mcp/ and the saved path is returned.

    Args:
        account: Account alias.
        message_id: Message ID returned by search_emails.
        attachment: The attachment's index (e.g. '0') or its filename, as
            shown by list_attachments.
    """
    return mail.read_attachment(account, message_id, attachment)


# --- Setup helpers for users without a terminal (e.g. Claude Desktop) -------

_PROVIDER_ENV_VARS = {
    "gmail": ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"),
    "outlook": ("MICROSOFT_CLIENT_ID", "MICROSOFT_TENANT_ID"),
}
_AUTH_MODULES = {"gmail": auth, "outlook": outlook_auth}


def _config_candidates() -> list[tuple[str, Path]]:
    """Config locations in lookup order (mirrors config.py's search order)."""
    candidates: list[tuple[str, Path]] = []
    env_path = os.environ.get("GMAIL_MCP_CONFIG")
    if env_path:
        candidates.append(("GMAIL_MCP_CONFIG", Path(env_path).expanduser()))
    candidates.append(("user config", Path.home() / ".gmail-mcp" / "config.json"))
    repo_cfg = Path(__file__).resolve().parent.parent.parent / "config.json"
    candidates.append(("repo config", repo_cfg))
    return candidates


@mcp.tool()
def setup_status() -> str:
    """Report setup state: config file location, accounts, auth status, missing env vars.

    Call this when list_accounts fails or an account is not authorized. It
    explains what is missing and what to do next (e.g. call authorize_account).
    """
    lines = ["Config file lookup (first existing file wins):"]
    for label, path in _config_candidates():
        mark = "found" if path.exists() else "missing"
        lines.append(f"  [{mark}] {path}  ({label})")

    try:
        accounts = {
            alias: config.resolve_account(alias)
            for alias in config.list_account_aliases()
        }
    except (config.ConfigError, ValueError, OSError) as e:
        lines += [
            "",
            f"Config could not be loaded: {e}",
            "Create ~/.gmail-mcp/config.json like:",
            '  {"accounts": {"personal": "you@gmail.com",',
            '                "work": {"email": "you@company.com", "provider": "outlook"}}}',
        ]
        accounts = {}

    providers_used = {a["provider"] for a in accounts.values()}
    authorized = {
        provider: {e.lower() for e in module.list_authorized_emails()}
        for provider, module in _AUTH_MODULES.items()
    }

    if accounts:
        lines += ["", "Accounts:"]
        for alias, acct in accounts.items():
            ok = acct["email"].lower() in authorized[acct["provider"]]
            status = "authorized" if ok else "NOT authorized -> call authorize_account"
            lines.append(f"  {alias}: {acct['email']} [{acct['provider']}] - {status}")

    lines += ["", f"Token store: {auth.DB_PATH}", "", "Environment variables:"]
    missing_needed = []
    for provider, names in _PROVIDER_ENV_VARS.items():
        for name in names:
            is_set = bool(os.environ.get(name))
            needed = provider in providers_used
            note = "set" if is_set else ("MISSING (needed)" if needed else "not set (unused)")
            lines.append(f"  {name}: {note}")
            if needed and not is_set:
                missing_needed.append(name)
    if missing_needed:
        lines += [
            "",
            "Set the missing values in the extension's settings "
            "(Claude Desktop) or the MCP server's env block, then restart the server.",
        ]
    return "\n".join(lines)


def _run_authorization(alias: str) -> str:
    acct = config.resolve_account(alias)
    backend = setup_auth._backend(acct["provider"])
    # The OAuth libraries print the sign-in URL to stdout, which would corrupt
    # the stdio MCP stream. Send it to stderr (the server log) instead.
    with contextlib.redirect_stdout(sys.stderr):
        actual = backend.authorize_account(email_hint=acct["email"])
    if actual.lower() != acct["email"].lower():
        return (
            f"Signed in as {actual}, but config lists {acct['email']} for alias "
            f"'{alias}'. Tokens were saved for {actual}. Re-run authorize_account "
            "and pick the right account, or update the config file."
        )
    return f"Authorized {actual} (alias '{alias}', provider {acct['provider']})."


@mcp.tool()
async def authorize_account(account: str) -> str:
    """Authorize a configured account by opening the provider's sign-in page in the browser.

    Runs the same OAuth flow as `gmail-mcp-auth add <alias>`. A browser
    window opens on this computer; the user must sign in with the matching
    account and approve. This call waits until sign-in finishes. Tell the
    user to look for the browser window before calling.

    Args:
        account: Account alias from the config file (see setup_status).
    """
    try:
        return await anyio.to_thread.run_sync(_run_authorization, account)
    except (config.ConfigError, auth.AuthError, outlook_auth.AuthError) as e:
        return f"Authorization failed: {e}\nCall setup_status for details."


if __name__ == "__main__":
    mcp.run()
