"""Attachment helpers shared by the Gmail and Outlook backends.

Handles the local sandbox directory, safe filename construction (so a
malicious attachment name can't escape the sandbox via path traversal),
and PDF text extraction.
"""
from __future__ import annotations

import io
import re
from pathlib import Path

from pypdf import PdfReader

# All saved attachments land here — under the user's Downloads so it's easy
# to find and obviously not a system location.
SANDBOX = Path.home() / "Downloads" / "gmail-mcp"


def _ensure_sandbox() -> Path:
    SANDBOX.mkdir(parents=True, exist_ok=True)
    return SANDBOX


def safe_filename(name: str) -> str:
    """Reduce an arbitrary attachment name to a safe basename.

    Strips directory components and any characters that could be used for
    path traversal, so the result always stays inside the sandbox.
    """
    # Basename only — drops 'a/b/../c' and Windows-style paths.
    name = name.replace("\\", "/").split("/")[-1]
    # Sanitise stem and extension separately so a stem made entirely of
    # non-ASCII characters (e.g. '請求書.pdf') keeps its extension instead of
    # collapsing to just 'pdf'.
    stem, _, ext = name.rpartition(".")
    if not stem:  # no dot at all, or a dotfile like '.hidden'
        stem, ext = name.lstrip("."), ""
    # Conservative whitelist; collapse everything else to underscores.
    clean_stem = re.sub(r"[^A-Za-z0-9._-]", "_", stem).lstrip("._").rstrip(".")
    clean_ext = re.sub(r"[^A-Za-z0-9]", "", ext)
    if not clean_stem:
        if not stem:
            return "attachment"
        clean_stem = "attachment"  # had a name, but nothing safe survived
    return f"{clean_stem}.{clean_ext}" if clean_ext else clean_stem


def save_to_sandbox(data: bytes, name: str) -> Path:
    """Write bytes to the sandbox under a safe, non-clobbering filename."""
    _ensure_sandbox()
    base = safe_filename(name)
    dest = SANDBOX / base
    # Don't silently overwrite — add a numeric suffix if the name is taken.
    if dest.exists():
        stem, dot, ext = base.partition(".")
        i = 1
        while dest.exists():
            dest = SANDBOX / f"{stem}_{i}{dot}{ext}"
            i += 1
    dest.write_bytes(data)
    return dest


def extract_pdf_text(data: bytes) -> str:
    """Extract text from a PDF byte string. Returns a readable string."""
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        # Many PDFs are "encrypted" with an empty password (permissions only),
        # which decrypts fine. A real user password does not — bail clearly
        # rather than crashing.
        try:
            opened = reader.decrypt("")
        except Exception:
            opened = 0
        if not opened:
            return (
                "(PDF is password-protected — cannot extract text without "
                "the password)"
            )
    pages = []
    for i, page in enumerate(reader.pages, 1):
        text = (page.extract_text() or "").strip()
        pages.append(f"--- Page {i} ---\n{text}")
    body = "\n\n".join(pages).strip()
    return body or "(no extractable text — the PDF may be scanned images)"
