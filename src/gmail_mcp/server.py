"""MCP server entry point.

Local (stdio, for Claude Code):   uv run python -m gmail_mcp.server
Local (stdio, Claude Desktop extension): python -m gmail_mcp.desktop
Remote (HTTPS, claude.ai custom connector): uv run python -m gmail_mcp.remote

Exposes Gmail and Microsoft 365 / Outlook tools. Every tool takes an
`account` alias as defined in config.json (except `list_accounts` and
`search_all_accounts`); the mail module dispatches to the right backend
based on each account's provider.

The tools are registered by build_server() so the same set can be served
over stdio (the module-level `mcp` below) or over Streamable HTTP with
OAuth (see remote.py).
"""
from __future__ import annotations

import contextlib
import os
import sys
from typing import Any

import anyio
from mcp.server.fastmcp import FastMCP, Image

from . import auth, config, mail, outlook_auth, setup_auth


def build_server(*, remote: bool = False, **fastmcp_kwargs: Any) -> FastMCP:
    """Create a FastMCP server with every mail tool registered.

    remote=True changes read_attachment so non-text attachments are returned
    inline (images) instead of being saved to the server's disk, where the
    user could never open them. It also leaves out the local setup tools
    (setup_status, authorize_account): those open a browser on the server
    machine and reveal local file paths.
    """
    mcp = FastMCP("gmail-mcp", **fastmcp_kwargs)
    _register_tools(mcp, remote=remote)
    return mcp


def _register_tools(mcp: FastMCP, *, remote: bool) -> None:  # noqa: C901

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

    if remote:
        # structured_output=False: the result mixes text and image blocks,
        # which have no JSON-schema form.
        @mcp.tool(structured_output=False)
        def read_attachment(account: str, message_id: str, attachment: str) -> list[Any]:
            """Read one attachment from a message.

            PDFs are returned as extracted text. Plain-text and CSV files are
            returned inline. Images are returned as images. Other binary files
            (spreadsheets, archives, ...) cannot be shown and are described only.

            Args:
                account: Account alias.
                message_id: Message ID returned by search_emails.
                attachment: The attachment's index (e.g. '0') or its filename, as
                    shown by list_attachments.
            """
            result = mail.read_attachment_inline(account, message_id, attachment)
            if isinstance(result, str):
                return [result]
            caption, data, fmt = result
            return [caption, Image(data=data, format=fmt)]
    else:
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

    if not remote:
        _register_local_setup_tools(mcp)


# --- Setup tools for users without a terminal (e.g. Claude Desktop) ---------
# Local (stdio) only. Never register these on the remote connector:
# authorize_account runs an OAuth loopback flow and opens a browser on the
# machine running the server, and setup_status reports local paths.

_PROVIDER_ENV_VARS = {
    "gmail": ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"),
    "outlook": ("MICROSOFT_CLIENT_ID", "MICROSOFT_TENANT_ID"),
}
_AUTH_MODULES = {"gmail": auth, "outlook": outlook_auth}

_CONFIG_EXAMPLE = (
    '  {"accounts": {"personal": "you@gmail.com",\n'
    '                "work": {"email": "you@company.com", "provider": "outlook"}}}'
)


def _setup_status() -> str:
    cfg_path = config.CONFIG_PATH
    lines = [f"Config file: {cfg_path} ({'found' if cfg_path.exists() else 'missing'})"]

    try:
        accounts = {
            alias: config.resolve_account(alias)
            for alias in config.list_account_aliases()
        }
    except (config.ConfigError, ValueError, OSError) as e:
        lines += ["", f"Config could not be loaded: {e}",
                  f"Create {cfg_path} like:", _CONFIG_EXAMPLE]
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
    missing_needed = False
    for provider, names in _PROVIDER_ENV_VARS.items():
        for name in names:
            is_set = bool(os.environ.get(name))
            needed = provider in providers_used
            note = "set" if is_set else ("MISSING (needed)" if needed else "not set (unused)")
            lines.append(f"  {name}: {note}")
            missing_needed |= needed and not is_set
    if missing_needed:
        lines += ["", "Set the missing values in the extension's settings "
                  "(Claude Desktop) or the MCP server's env block, then restart the server."]
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


def _register_local_setup_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    def setup_status() -> str:
        """Report setup state: config file location, accounts, auth status, missing env vars.

        Call this when list_accounts fails or an account is not authorized. It
        explains what is missing and what to do next (e.g. call authorize_account).
        """
        return _setup_status()

    @mcp.tool()
    async def authorize_account(account: str) -> str:
        """Authorize a configured account by opening the provider's sign-in page in the browser.

        Runs the same OAuth flow as `gmail-mcp-setup add <alias>`. A browser
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


# Local stdio server used by Claude Code.
mcp = build_server()


if __name__ == "__main__":
    mcp.run()
