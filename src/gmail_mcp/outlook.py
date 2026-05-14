"""Microsoft 365 / Outlook API wrapper via Microsoft Graph REST.

Mirrors the function surface of gmail.py (search, read_message, read_thread,
create_draft, send_draft, list_labels, modify_labels) so the MCP tools can
dispatch to either provider. Returns human-readable strings for MCP results.

Query syntax note: Outlook uses Microsoft Graph's KQL-style $search syntax
(e.g. 'from:bob@x.com subject:invoice'), NOT Gmail's query syntax. Gmail
operators like 'is:unread' or 'after:2025/01/01' will not work here.
"""
from __future__ import annotations

import json

import httpx

from . import config, outlook_auth

GRAPH = "https://graph.microsoft.com/v1.0"
# Ask Graph to hand us plain-text bodies instead of HTML.
_TEXT_BODY = {"Prefer": 'outlook.body-content-type="text"'}


def _token(alias: str) -> str:
    email = config.resolve_alias(alias)
    return outlook_auth.get_access_token(email)


def _request(method: str, alias: str, path: str, **kwargs) -> httpx.Response:
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {_token(alias)}"
    url = path if path.startswith("http") else f"{GRAPH}{path}"
    resp = httpx.request(method, url, headers=headers, timeout=30.0, **kwargs)
    if resp.status_code >= 400:
        raise RuntimeError(
            f"Graph API {method} {path} failed ({resp.status_code}): "
            f"{resp.text[:300]}"
        )
    return resp


def _fmt_addr(addr_obj: dict | None) -> str:
    """Format a Graph emailAddress wrapper like {'emailAddress': {...}}."""
    if not addr_obj:
        return ""
    addr = addr_obj.get("emailAddress", {})
    name, email = addr.get("name", ""), addr.get("address", "")
    return f"{name} <{email}>" if name and name != email else email


def _fmt_recipients(recips: list[dict] | None) -> str:
    return ", ".join(_fmt_addr(r) for r in (recips or []))


def _recipient_list(comma_separated: str) -> list[dict]:
    """Turn 'a@x.com, b@y.com' into Graph recipient objects."""
    return [
        {"emailAddress": {"address": e.strip()}}
        for e in comma_separated.split(",")
        if e.strip()
    ]


def search(alias: str, query: str, max_results: int = 20) -> str:
    params = {
        "$search": f'"{query}"',
        "$top": str(max_results),
        "$select": "id,conversationId,subject,from,receivedDateTime,bodyPreview",
    }
    resp = _request("GET", alias, "/me/messages", params=params)
    msgs = resp.json().get("value", [])
    if not msgs:
        return f"No messages matched '{query}' in {alias}."

    lines = [f"Found {len(msgs)} messages in {alias} for: {query}\n"]
    for m in msgs:
        lines.append(
            f"  [{m['id']}] {m.get('receivedDateTime', '')}\n"
            f"      From: {_fmt_addr(m.get('from'))}\n"
            f"      Subj: {m.get('subject', '')}\n"
            f"      Thread: {m.get('conversationId', '')}\n"
            f"      Snippet: {(m.get('bodyPreview') or '')[:120]}"
        )
    return "\n".join(lines)


def read_message(alias: str, message_id: str) -> str:
    params = {
        "$select": "subject,from,toRecipients,ccRecipients,receivedDateTime,body",
    }
    resp = _request(
        "GET", alias, f"/me/messages/{message_id}", params=params, headers=dict(_TEXT_BODY)
    )
    m = resp.json()
    return (
        f"From: {_fmt_addr(m.get('from'))}\n"
        f"To: {_fmt_recipients(m.get('toRecipients'))}\n"
        f"Cc: {_fmt_recipients(m.get('ccRecipients'))}\n"
        f"Date: {m.get('receivedDateTime', '')}\n"
        f"Subject: {m.get('subject', '')}\n"
        f"\n{(m.get('body') or {}).get('content', '(no readable body)')}"
    )


def read_thread(alias: str, thread_id: str) -> str:
    """Read every message in a conversation. thread_id is a Graph conversationId."""
    params = {
        "$filter": f"conversationId eq '{thread_id}'",
        "$orderby": "receivedDateTime",
        "$select": "id,subject,from,receivedDateTime,body",
    }
    resp = _request(
        "GET", alias, "/me/messages", params=params, headers=dict(_TEXT_BODY)
    )
    msgs = resp.json().get("value", [])
    if not msgs:
        return f"No messages found for thread {thread_id} in {alias}."

    out = [f"Thread {thread_id} ({len(msgs)} messages):\n"]
    for i, m in enumerate(msgs, 1):
        out.append(
            f"--- Message {i} [{m['id']}] ---\n"
            f"From: {_fmt_addr(m.get('from'))}\n"
            f"Date: {m.get('receivedDateTime', '')}\n"
            f"Subject: {m.get('subject', '')}\n\n"
            f"{(m.get('body') or {}).get('content', '(no readable body)')}\n"
        )
    return "\n".join(out)


def create_draft(alias: str, to: str, subject: str, body: str,
                  cc: str = "", bcc: str = "") -> str:
    message: dict = {
        "subject": subject,
        "body": {"contentType": "text", "content": body},
        "toRecipients": _recipient_list(to),
    }
    if cc:
        message["ccRecipients"] = _recipient_list(cc)
    if bcc:
        message["bccRecipients"] = _recipient_list(bcc)

    resp = _request(
        "POST", alias, "/me/messages",
        headers={"Content-Type": "application/json"},
        content=json.dumps(message),
    )
    draft = resp.json()
    return (
        f"✅ Draft created in {alias}\n"
        f"  Draft ID: {draft['id']}\n"
        f"  To: {to}\n  Subject: {subject}\n"
        f"\nUse send_draft with this draft_id to send."
    )


def send_draft(alias: str, draft_id: str) -> str:
    _request("POST", alias, f"/me/messages/{draft_id}/send")
    return f"✅ Sent draft {draft_id} from {alias}."


def list_labels(alias: str) -> str:
    """List mail folders and categories — Outlook's closest analogues to labels."""
    folders = _request(
        "GET", alias, "/me/mailFolders",
        params={"$select": "id,displayName", "$top": "100"},
    ).json().get("value", [])
    cats = _request(
        "GET", alias, "/me/outlook/masterCategories",
        params={"$select": "displayName"},
    ).json().get("value", [])

    lines = [f"Folders in {alias}:"]
    for f in sorted(folders, key=lambda x: x.get("displayName", "")):
        lines.append(f"  {f['id']:30s}  {f.get('displayName', '')}")
    lines.append(f"\nCategories in {alias}:")
    for c in sorted(cats, key=lambda x: x.get("displayName", "")):
        lines.append(f"  {c.get('displayName', '')}")
    return "\n".join(lines)


def modify_labels(alias: str, message_id: str,
                  add: list[str] | None = None,
                  remove: list[str] | None = None) -> str:
    """Add/remove Outlook categories on a message.

    The special token 'UNREAD' is handled like Gmail's: add=['UNREAD'] marks
    the message unread, remove=['UNREAD'] marks it read. Every other token is
    treated as an Outlook category. (Outlook has no direct equivalent to
    Gmail's INBOX/STARRED/TRASH system labels — moving folders or flagging is
    a separate operation.)
    """
    add = list(add or [])
    remove = list(remove or [])
    patch: dict = {}

    if "UNREAD" in add:
        patch["isRead"] = False
        add.remove("UNREAD")
    if "UNREAD" in remove:
        patch["isRead"] = True
        remove.remove("UNREAD")

    if add or remove:
        current = _request(
            "GET", alias, f"/me/messages/{message_id}",
            params={"$select": "categories"},
        ).json().get("categories", [])
        updated = [c for c in current if c not in remove]
        for c in add:
            if c not in updated:
                updated.append(c)
        patch["categories"] = updated

    if not patch:
        return f"No changes requested for {message_id} in {alias}."

    _request(
        "PATCH", alias, f"/me/messages/{message_id}",
        headers={"Content-Type": "application/json"},
        content=json.dumps(patch),
    )
    return (
        f"✅ Modified {message_id} in {alias}. "
        f"Added: {add or []}, Removed: {remove or []}"
        + (", marked " + ("unread" if patch.get("isRead") is False else "read")
           if "isRead" in patch else "")
    )
