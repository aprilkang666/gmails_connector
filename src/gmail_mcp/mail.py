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

from . import attachments, auth, config, gmail, outlook, outlook_auth

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


def list_attachments(alias: str, message_id: str) -> str:
    return _backend(alias).list_attachments(alias, message_id)


# Image types a client can render inline, mapped to Image() format names.
_INLINE_IMAGE_FORMATS = {
    "image/png": "png", "image/jpeg": "jpeg", "image/jpg": "jpeg",
    "image/gif": "gif", "image/webp": "webp",
}
# Keep inline images well under typical MCP / model payload limits.
_MAX_INLINE_IMAGE_BYTES = 5 * 1024 * 1024


def _read_text_like(filename: str, mime_type: str, data: bytes) -> str | None:
    """Return readable text for PDF / text attachments, else None."""
    lower = filename.lower()
    if mime_type == "application/pdf" or lower.endswith(".pdf"):
        text = attachments.extract_pdf_text(data)
        return f"📄 {filename} ({len(data)} bytes, PDF)\n\n{text}"
    if mime_type.startswith("text/") or lower.endswith(
        (".txt", ".csv", ".md", ".json", ".log")
    ):
        decoded = data.decode("utf-8", errors="replace")
        return f"📄 {filename} ({len(data)} bytes, text)\n\n{decoded}"
    return None


def read_attachment(alias: str, message_id: str, identifier: str) -> str:
    """Fetch an attachment and return readable content (local mode).

    PDF → extracted text. text/CSV → decoded inline. Anything else → saved to
    the sandbox (~/Downloads/gmail-mcp/) with the path returned.
    """
    filename, mime_type, data = _backend(alias).get_attachment(
        alias, message_id, identifier
    )
    text = _read_text_like(filename, mime_type, data)
    if text is not None:
        return text

    path = attachments.save_to_sandbox(data, filename)
    return (
        f"📎 {filename} ({len(data)} bytes, {mime_type or 'unknown type'})\n"
        f"Not a text or PDF type — saved to: {path}"
    )


def read_attachment_inline(
    alias: str, message_id: str, identifier: str
) -> str | tuple[str, bytes, str]:
    """Fetch an attachment without touching the server's disk (remote mode).

    Returns text for PDF / text files, (caption, bytes, format) for images
    small enough to return inline, and a description for anything else.
    """
    filename, mime_type, data = _backend(alias).get_attachment(
        alias, message_id, identifier
    )
    text = _read_text_like(filename, mime_type, data)
    if text is not None:
        return text

    kind = mime_type or "unknown type"
    fmt = _INLINE_IMAGE_FORMATS.get((mime_type or "").lower())
    if fmt and len(data) <= _MAX_INLINE_IMAGE_BYTES:
        return (f"🖼️ {filename} ({len(data)} bytes, {kind})", data, fmt)
    if fmt:
        return (
            f"📎 {filename} ({len(data)} bytes, {kind})\n"
            f"Image is larger than {_MAX_INLINE_IMAGE_BYTES // (1024 * 1024)} MB "
            "and can't be returned inline. Open it in the mail client."
        )
    return (
        f"📎 {filename} ({len(data)} bytes, {kind})\n"
        "This file type can't be read through the remote connector. "
        "Open it in the mail client."
    )


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
