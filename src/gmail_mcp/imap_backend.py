"""IMAP/SMTP backend for password-authenticated email providers.

Mirrors the function surface of gmail.py and outlook.py so the MCP tools can
dispatch to any provider. Returns human-readable strings for MCP results.

Server hosts are resolved from PRESETS: by the account's "preset" key if set,
otherwise by the email domain. Any of imap_host/imap_port/smtp_host/smtp_port
in config.json overrides the preset, which is what custom domains need.
"""
from __future__ import annotations

import base64
import email
import email.utils
import imaplib
import re
import smtplib
import ssl
from email.header import decode_header
from email.mime.text import MIMEText
from typing import Any

from . import config, imap_auth

PRESETS = {
    "gmail": ("imap.gmail.com", 993, "smtp.gmail.com", 465),
    "fastmail": ("imap.fastmail.com", 993, "smtp.fastmail.com", 465),
    "yahoo": ("imap.mail.yahoo.com", 993, "smtp.mail.yahoo.com", 465),
    "zoho": ("imap.zoho.com", 993, "smtp.zoho.com", 465),
    "icloud": ("imap.mail.me.com", 993, "smtp.mail.me.com", 587),
    "alibaba": ("imap.mxhichina.com", 993, "smtp.mxhichina.com", 465),
    "qq": ("imap.qq.com", 993, "smtp.qq.com", 465),
    "163": ("imap.163.com", 993, "smtp.163.com", 465),
}

# Email domain -> preset name, so the common case needs no host config at all.
DOMAIN_PRESETS = {
    "gmail.com": "gmail",
    "googlemail.com": "gmail",
    "fastmail.com": "fastmail",
    "fastmail.fm": "fastmail",
    "yahoo.com": "yahoo",
    "yahoo.co.uk": "yahoo",
    "ymail.com": "yahoo",
    "zoho.com": "zoho",
    "zohomail.com": "zoho",
    "icloud.com": "icloud",
    "me.com": "icloud",
    "mac.com": "icloud",
    "aliyun.com": "alibaba",
    "qq.com": "qq",
    "163.com": "163",
}


def _server_settings(alias: str) -> dict:
    acct = config.resolve_account(alias)
    email_addr = acct["email"]
    explicit = {k: acct[k] for k in
                ("imap_host", "imap_port", "smtp_host", "smtp_port") if k in acct}

    preset_name = acct.get("preset") or DOMAIN_PRESETS.get(
        email_addr.rpartition("@")[2].lower()
    )
    if preset_name and preset_name not in PRESETS:
        raise config.ConfigError(
            f"Account '{alias}' has unknown preset '{preset_name}'. "
            f"Known presets: {', '.join(sorted(PRESETS))}."
        )

    if preset_name:
        imap_host, imap_port, smtp_host, smtp_port = PRESETS[preset_name]
        defaults = {"imap_host": imap_host, "imap_port": imap_port,
                    "smtp_host": smtp_host, "smtp_port": smtp_port}
    elif {"imap_host", "smtp_host"} <= explicit.keys():
        defaults = {"imap_port": 993, "smtp_port": 465}
    else:
        raise config.ConfigError(
            f"Cannot determine mail servers for '{alias}' ({email_addr}). "
            f"Add \"preset\" (one of: {', '.join(sorted(PRESETS))}) or set "
            "\"imap_host\" and \"smtp_host\" for this account in config.json."
        )

    settings = {**defaults, **explicit}
    return {
        "email": email_addr,
        "imap_host": settings["imap_host"],
        "imap_port": int(settings["imap_port"]),
        "smtp_host": settings["smtp_host"],
        "smtp_port": int(settings["smtp_port"]),
    }


def _smtp_connect(settings: dict, password: str) -> smtplib.SMTP:
    """Connect and authenticate. Port 587 uses STARTTLS, others implicit TLS."""
    context = ssl.create_default_context()
    if int(settings["smtp_port"]) == 587:
        smtp = smtplib.SMTP(settings["smtp_host"], settings["smtp_port"])
        smtp.starttls(context=context)
    else:
        smtp = smtplib.SMTP_SSL(
            settings["smtp_host"], settings["smtp_port"], context=context
        )
    smtp.login(settings["email"], password)
    return smtp


def _imap_connect(alias: str) -> imaplib.IMAP4_SSL:
    settings = _server_settings(alias)
    password = imap_auth.get_password(settings["email"])
    conn = imaplib.IMAP4_SSL(settings["imap_host"], settings["imap_port"])
    conn.login(settings["email"], password)
    return conn


def _decode_header_value(raw: str | None) -> str:
    if not raw:
        return ""
    parts = decode_header(raw)
    decoded = []
    for data, charset in parts:
        if isinstance(data, bytes):
            decoded.append(data.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(data)
    return " ".join(decoded)


def _get_body(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    html = payload.decode(charset, errors="replace")
                    return re.sub(r"<[^>]+>", "", html)
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace")
            if msg.get_content_type() == "text/html":
                return re.sub(r"<[^>]+>", "", text)
            return text
    return "(no readable body)"


def _format_date(msg: email.message.Message) -> str:
    raw = msg.get("Date", "")
    parsed = email.utils.parsedate_to_datetime(raw) if raw else None
    if parsed:
        return parsed.strftime("%Y-%m-%d %H:%M:%S")
    return raw


def search(alias: str, query: str, max_results: int = 20) -> str:
    conn = _imap_connect(alias)
    conn.select("INBOX", readonly=True)

    imap_query = _to_imap_search(query)
    _, data = conn.search("UTF-8", *imap_query)
    msg_ids = data[0].split()

    if not msg_ids:
        conn.logout()
        return f"No messages matched '{query}' in {alias}."

    msg_ids = msg_ids[-max_results:]
    msg_ids.reverse()

    lines = [f"Found {len(msg_ids)} messages in {alias} for: {query}\n"]
    for mid in msg_ids:
        _, msg_data = conn.fetch(mid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
        if not msg_data or not msg_data[0]:
            continue
        raw = msg_data[0][1] if isinstance(msg_data[0], tuple) else msg_data[0]
        if isinstance(raw, bytes):
            hdr = email.message_from_bytes(raw)
        else:
            continue
        uid = mid.decode() if isinstance(mid, bytes) else str(mid)
        lines.append(
            f"  [{uid}] {_decode_header_value(hdr.get('Date', ''))}\n"
            f"      From: {_decode_header_value(hdr.get('From', ''))}\n"
            f"      Subj: {_decode_header_value(hdr.get('Subject', ''))}"
        )

    conn.logout()
    return "\n".join(lines)


def _to_imap_search(query: str) -> list[str]:
    """Convert a simple query string to IMAP SEARCH arguments.

    Supports basic patterns like 'from:x subject:y' and falls back to a
    full-text SUBJECT search for plain keywords.
    """
    q = query.strip()
    if not q or q.upper() == "ALL":
        return ["ALL"]

    parts: list[str] = []
    tokens = q.split()
    plain_words = []

    for token in tokens:
        lower = token.lower()
        if lower.startswith("from:"):
            parts.extend(["FROM", f'"{token[5:]}"'])
        elif lower.startswith("to:"):
            parts.extend(["TO", f'"{token[3:]}"'])
        elif lower.startswith("subject:"):
            parts.extend(["SUBJECT", f'"{token[8:]}"'])
        elif lower == "is:unread":
            parts.append("UNSEEN")
        elif lower == "is:read":
            parts.append("SEEN")
        elif lower.startswith("after:"):
            parts.extend(["SINCE", token[6:]])
        elif lower.startswith("before:"):
            parts.extend(["BEFORE", token[7:]])
        else:
            plain_words.append(token)

    if plain_words:
        parts.extend(["SUBJECT", f'"{" ".join(plain_words)}"'])

    return parts


def read_message(alias: str, message_id: str) -> str:
    conn = _imap_connect(alias)
    conn.select("INBOX", readonly=True)

    _, msg_data = conn.fetch(message_id.encode(), "(RFC822)")
    conn.logout()

    if not msg_data or not msg_data[0]:
        return f"Message {message_id} not found in {alias}."

    raw = msg_data[0][1]
    msg = email.message_from_bytes(raw)
    body = _get_body(msg)

    return (
        f"From: {_decode_header_value(msg.get('From'))}\n"
        f"To: {_decode_header_value(msg.get('To'))}\n"
        f"Date: {_format_date(msg)}\n"
        f"Subject: {_decode_header_value(msg.get('Subject'))}\n"
        f"\n{body}"
    )


def read_thread(alias: str, thread_id: str) -> str:
    """IMAP has no native thread concept. Read the single message."""
    return read_message(alias, thread_id)


def create_draft(alias: str, to: str, subject: str, body: str,
                 cc: str = "", bcc: str = "") -> str:
    settings = _server_settings(alias)
    msg = MIMEText(body, _charset="utf-8")
    msg["From"] = settings["email"]
    msg["To"] = to
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc

    conn = _imap_connect(alias)
    conn.select("INBOX")
    conn.append("Drafts", "\\Draft", None, msg.as_bytes())
    conn.logout()

    return (
        f"✅ Draft created in {alias}\n"
        f"  To: {to}\n  Subject: {subject}\n"
        f"\nNote: IMAP drafts have no draft_id. Use send_draft with "
        f"'latest' to send the most recent draft, or send directly."
    )


def send_draft(alias: str, draft_id: str) -> str:
    """Send an email directly via SMTP (IMAP has no send-draft concept)."""
    settings = _server_settings(alias)

    conn = _imap_connect(alias)
    conn.select("Drafts")
    _, data = conn.search(None, "ALL")
    msg_ids = data[0].split()
    if not msg_ids:
        conn.logout()
        return f"No drafts found in {alias}."

    target = msg_ids[-1]
    _, msg_data = conn.fetch(target, "(RFC822)")
    conn.logout()

    raw = msg_data[0][1]
    msg = email.message_from_bytes(raw)

    to_addrs = []
    for field in ("To", "Cc", "Bcc"):
        val = msg.get(field, "")
        if val:
            to_addrs.extend(addr.strip() for addr in val.split(",") if addr.strip())

    password = imap_auth.get_password(settings["email"])
    with _smtp_connect(settings, password) as smtp:
        smtp.sendmail(settings["email"], to_addrs, raw)

    return f"✅ Sent latest draft from {alias} to {', '.join(to_addrs)}."


def list_labels(alias: str) -> str:
    conn = _imap_connect(alias)
    _, folders = conn.list()
    conn.logout()

    lines = [f"Folders in {alias}:"]
    for f in (folders or []):
        if isinstance(f, bytes):
            lines.append(f"  {f.decode('utf-8', errors='replace')}")
    return "\n".join(lines)


def modify_labels(alias: str, message_id: str,
                  add: list[str] | None = None,
                  remove: list[str] | None = None) -> str:
    conn = _imap_connect(alias)
    conn.select("INBOX")

    mid = message_id.encode()
    if add:
        for flag in add:
            if flag == "UNREAD":
                conn.store(mid, "-FLAGS", "\\Seen")
            else:
                conn.store(mid, "+FLAGS", f"\\{flag}")
    if remove:
        for flag in remove:
            if flag == "UNREAD":
                conn.store(mid, "+FLAGS", "\\Seen")
            else:
                conn.store(mid, "-FLAGS", f"\\{flag}")

    conn.logout()
    return f"✅ Modified flags on {message_id} in {alias}. Added: {add or []}, Removed: {remove or []}"


def _get_attachments(msg: email.message.Message) -> list[dict]:
    atts = []
    for i, part in enumerate(msg.walk()):
        filename = part.get_filename()
        if filename:
            atts.append({
                "index": i,
                "filename": _decode_header_value(filename),
                "mime_type": part.get_content_type(),
                "size": len(part.get_payload(decode=True) or b""),
            })
    return atts


def list_attachments(alias: str, message_id: str) -> str:
    conn = _imap_connect(alias)
    conn.select("INBOX", readonly=True)
    _, msg_data = conn.fetch(message_id.encode(), "(RFC822)")
    conn.logout()

    if not msg_data or not msg_data[0]:
        return f"Message {message_id} not found in {alias}."

    msg = email.message_from_bytes(msg_data[0][1])
    atts = _get_attachments(msg)
    if not atts:
        return f"No attachments on message {message_id} in {alias}."

    lines = [f"Attachments on {message_id} in {alias}:"]
    for idx, a in enumerate(atts):
        lines.append(
            f"  [{idx}] {a['filename']}\n"
            f"      Type: {a['mime_type']}  Size: {a['size']} bytes"
        )
    lines.append("\nUse read_attachment with the index or filename.")
    return "\n".join(lines)


def get_attachment(alias: str, message_id: str, identifier: str) -> tuple[str, str, bytes]:
    conn = _imap_connect(alias)
    conn.select("INBOX", readonly=True)
    _, msg_data = conn.fetch(message_id.encode(), "(RFC822)")
    conn.logout()

    if not msg_data or not msg_data[0]:
        raise ValueError(f"Message {message_id} not found in {alias}.")

    msg = email.message_from_bytes(msg_data[0][1])
    atts = _get_attachments(msg)
    if not atts:
        raise ValueError(f"No attachments on message {message_id} in {alias}.")

    match = None
    if isinstance(identifier, str) and identifier.isdigit():
        idx = int(identifier)
        if 0 <= idx < len(atts):
            match = atts[idx]
    if match is None:
        match = next((a for a in atts if a["filename"] == identifier), None)
    if match is None:
        match = next(
            (a for a in atts if a["filename"].lower() == str(identifier).lower()),
            None,
        )
    if match is None:
        names = ", ".join(f"[{i}] {a['filename']}" for i, a in enumerate(atts))
        raise ValueError(
            f"No attachment matching {identifier!r} on {message_id} in {alias}. "
            f"Available: {names}"
        )

    for i, part in enumerate(msg.walk()):
        if i == match["index"]:
            data = part.get_payload(decode=True) or b""
            return match["filename"], match["mime_type"], data

    raise ValueError(f"Could not extract attachment data for {match['filename']}.")
