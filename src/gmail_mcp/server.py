"""MCP server entry point. Run with: uv run python -m gmail_mcp.server

Exposes Gmail tools to Claude Code. Every tool takes an `account` alias as
defined in config.json, except `list_accounts` and `search_all_accounts`.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from . import gmail

mcp = FastMCP("gmail-mcp")


@mcp.tool()
def list_accounts() -> str:
    """List all configured Gmail accounts and whether each is authorized.

    Always call this first to see what account aliases are available.
    """
    return gmail.list_all_accounts()


@mcp.tool()
def search_emails(account: str, query: str, max_results: int = 20) -> str:
    """Search one Gmail account using Gmail's query syntax.

    Examples of `query`:
      - 'from:boss@example.com is:unread'
      - 'subject:"quarterly review" after:2026/01/01'
      - 'has:attachment filename:pdf'

    Args:
        account: Account alias (e.g. 'personal', 'work') — see list_accounts.
        query: Gmail search query.
        max_results: Cap on messages returned (default 20).

    Returns a formatted list of matching messages with IDs, senders, subjects, snippets.
    Use the message IDs with read_email.
    """
    return gmail.search(account, query, max_results)


@mcp.tool()
def search_all_accounts(query: str, max_per_account: int = 10) -> str:
    """Run the same search across every authorized account.

    Useful for 'do I have any unread emails from X' across personal + work.
    """
    return gmail.search_all(query, max_per_account)


@mcp.tool()
def read_email(account: str, message_id: str) -> str:
    """Read the full body of a single message by its ID.

    Args:
        account: Account alias.
        message_id: Message ID returned by search_emails.
    """
    return gmail.read_message(account, message_id)


@mcp.tool()
def read_thread(account: str, thread_id: str) -> str:
    """Read an entire email thread (parent + all replies) by its thread ID.

    Note: a message's thread_id is often the same as its message_id for
    single-message threads. For multi-message threads, search results include both.
    """
    return gmail.read_thread(account, thread_id)


@mcp.tool()
def create_draft(account: str, to: str, subject: str, body: str,
                 cc: str = "", bcc: str = "") -> str:
    """Create a draft email in the specified account. Does NOT send.

    Always create a draft first. The user reviews it in Gmail, then calls
    send_draft explicitly.

    Args:
        account: Account alias to send FROM.
        to: Recipient email(s), comma-separated.
        subject: Email subject.
        body: Plain text body. Use \\n for line breaks.
        cc: Optional CC recipients.
        bcc: Optional BCC recipients.
    """
    return gmail.create_draft(account, to, subject, body, cc, bcc)


@mcp.tool()
def send_draft(account: str, draft_id: str) -> str:
    """Send a previously created draft. This ACTUALLY sends the email.

    Only call after the user has confirmed they want to send the draft.

    Args:
        account: Same account the draft was created in.
        draft_id: ID returned by create_draft.
    """
    return gmail.send_draft(account, draft_id)


@mcp.tool()
def list_labels(account: str) -> str:
    """List all labels (folders) in an account. Returns label IDs and names.

    Use the label IDs with modify_labels.
    """
    return gmail.list_labels(account)


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
    return gmail.modify_labels(account, message_id, add, remove)


if __name__ == "__main__":
    mcp.run()
