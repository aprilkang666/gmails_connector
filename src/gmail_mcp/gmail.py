"""Gmail API wrapper. Each function is account-scoped via an alias.

Returns human-readable strings for MCP results (LLMs parse these better
than nested JSON).
"""
from __future__ import annotations

import base64
from email.mime.text import MIMEText
from typing import Any

from googleapiclient.discovery import build

from . import auth, config


def _service(alias: str):
    email = config.resolve_alias(alias)
    creds = auth.get_credentials(email)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _decode_body(payload: dict) -> str:
    """Walk the MIME tree and return the first substantial text/plain body found.

    Some senders include a tiny placeholder text/plain part (e.g. " " or
    "View in browser") next to a real text/html body. Skip text/plain parts
    whose stripped content is too short to be the real message, and fall
    through to the text/html walker.
    """
    MIN_PLAIN_CHARS = 50

    def walk(part: dict) -> str | None:
        mime = part.get("mimeType", "")
        body = part.get("body", {})
        data = body.get("data")
        if mime == "text/plain" and data:
            decoded = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            if len(decoded.strip()) >= MIN_PLAIN_CHARS:
                return decoded
        for sub in part.get("parts", []) or []:
            found = walk(sub)
            if found:
                return found
        return None

    # Fallback: if no text/plain found, try text/html (stripped crudely)
    text = walk(payload)
    if text:
        return text

    def walk_html(part: dict) -> str | None:
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if mime == "text/html" and data:
            import re
            html = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            return re.sub(r"<[^>]+>", "", html)
        for sub in part.get("parts", []) or []:
            found = walk_html(sub)
            if found:
                return found
        return None

    return walk_html(payload) or "(no readable body)"


def _header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def search(alias: str, query: str, max_results: int = 20) -> str:
    svc = _service(alias)
    resp = svc.users().messages().list(
        userId="me", q=query, maxResults=max_results
    ).execute()
    msgs = resp.get("messages", [])
    if not msgs:
        return f"No messages matched '{query}' in {alias}."

    lines = [f"Found {len(msgs)} messages in {alias} for: {query}\n"]
    for m in msgs:
        meta = svc.users().messages().get(
            userId="me", id=m["id"], format="metadata",
            metadataHeaders=["From", "Subject", "Date"],
        ).execute()
        headers = meta["payload"]["headers"]
        lines.append(
            f"  [{m['id']}] {_header(headers, 'Date')}\n"
            f"      From: {_header(headers, 'From')}\n"
            f"      Subj: {_header(headers, 'Subject')}\n"
            f"      Snippet: {meta.get('snippet', '')[:120]}"
        )
    return "\n".join(lines)


def read_message(alias: str, message_id: str) -> str:
    svc = _service(alias)
    msg = svc.users().messages().get(
        userId="me", id=message_id, format="full"
    ).execute()
    headers = msg["payload"]["headers"]
    body = _decode_body(msg["payload"])
    return (
        f"From: {_header(headers, 'From')}\n"
        f"To: {_header(headers, 'To')}\n"
        f"Date: {_header(headers, 'Date')}\n"
        f"Subject: {_header(headers, 'Subject')}\n"
        f"\n{body}"
    )


def read_thread(alias: str, thread_id: str) -> str:
    svc = _service(alias)
    thread = svc.users().threads().get(userId="me", id=thread_id, format="full").execute()
    out = [f"Thread {thread_id} ({len(thread['messages'])} messages):\n"]
    for i, msg in enumerate(thread["messages"], 1):
        headers = msg["payload"]["headers"]
        body = _decode_body(msg["payload"])
        out.append(
            f"--- Message {i} [{msg['id']}] ---\n"
            f"From: {_header(headers, 'From')}\n"
            f"Date: {_header(headers, 'Date')}\n"
            f"Subject: {_header(headers, 'Subject')}\n\n"
            f"{body}\n"
        )
    return "\n".join(out)


def create_draft(alias: str, to: str, subject: str, body: str,
                 cc: str = "", bcc: str = "") -> str:
    svc = _service(alias)
    msg = MIMEText(body)
    msg["to"] = to
    msg["subject"] = subject
    if cc:
        msg["cc"] = cc
    if bcc:
        msg["bcc"] = bcc

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    draft = svc.users().drafts().create(
        userId="me", body={"message": {"raw": raw}}
    ).execute()
    return (
        f"✅ Draft created in {alias}\n"
        f"  Draft ID: {draft['id']}\n"
        f"  To: {to}\n  Subject: {subject}\n"
        f"\nUse send_draft with this draft_id to send."
    )


def send_draft(alias: str, draft_id: str) -> str:
    svc = _service(alias)
    sent = svc.users().drafts().send(userId="me", body={"id": draft_id}).execute()
    return f"✅ Sent draft {draft_id} from {alias}. Message ID: {sent['id']}"


def list_labels(alias: str) -> str:
    svc = _service(alias)
    labels = svc.users().labels().list(userId="me").execute().get("labels", [])
    lines = [f"Labels in {alias}:"]
    for lbl in sorted(labels, key=lambda x: x["name"]):
        lines.append(f"  {lbl['id']:30s}  {lbl['name']}")
    return "\n".join(lines)


def modify_labels(alias: str, message_id: str,
                  add: list[str] | None = None,
                  remove: list[str] | None = None) -> str:
    svc = _service(alias)
    body: dict[str, Any] = {}
    if add:
        body["addLabelIds"] = add
    if remove:
        body["removeLabelIds"] = remove
    svc.users().messages().modify(userId="me", id=message_id, body=body).execute()
    return f"✅ Modified labels on {message_id} in {alias}. Added: {add or []}, Removed: {remove or []}"
