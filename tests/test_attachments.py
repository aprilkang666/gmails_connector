"""attachments.py: PDF text extraction (incl. encrypted PDFs, 94a2541),
filename sanitising, and sandboxed saving. Test PDFs are generated in-process."""

from __future__ import annotations

import io

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from gmail_mcp import attachments


def make_pdf(pages: list[str], user_password: str | None = None, owner_password: str | None = None) -> bytes:
    """Build a minimal PDF whose pages draw the given text with Helvetica."""
    writer = PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=300, height=200)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
        )
        stream = DecodedStreamObject()
        if text:
            stream.set_data(f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    if user_password is not None:
        writer.encrypt(user_password, owner_password or "owner-secret", algorithm="RC4-128")
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_extract_text_multi_page():
    out = attachments.extract_pdf_text(make_pdf(["Hello invoice", "Second page"]))
    assert "--- Page 1 ---\nHello invoice" in out
    assert "--- Page 2 ---\nSecond page" in out


def test_extract_empty_password_encrypted_pdf_still_readable():
    """Permissions-only encryption (empty user password) must decrypt."""
    data = make_pdf(["Owner locked"], user_password="")
    out = attachments.extract_pdf_text(data)
    assert "Owner locked" in out


def test_extract_password_protected_pdf_returns_message():
    """Regression for 94a2541: a real user password must not crash."""
    data = make_pdf(["Top secret"], user_password="hunter2")
    out = attachments.extract_pdf_text(data)
    assert "password-protected" in out
    assert "Top secret" not in out


def test_extract_pdf_without_text():
    out = attachments.extract_pdf_text(make_pdf([""]))
    assert out == "--- Page 1 ---"  # page header only; no text found on page


@pytest.mark.parametrize(
    "name,expected",
    [
        ("report.pdf", "report.pdf"),
        ("../../etc/passwd", "passwd"),
        ("..\\..\\Windows\\evil.exe", "evil.exe"),
        ("my file (1).png", "my_file__1_.png"),
        ("..", "attachment"),
        ("", "attachment"),
        (".hidden", "hidden"),
    ],
)
def test_safe_filename(name, expected):
    assert attachments.safe_filename(name) == expected


@pytest.mark.parametrize(
    "name,expected",
    [
        ("請求書.pdf", "attachment.pdf"),
        ("日本.txt", "attachment.txt"),
        ("請求書2026.pdf", "2026.pdf"),
        ("報告 final.docx", "final.docx"),
        ("archive.tar.gz", "archive.tar.gz"),
        ("file.", "file"),
        ("weird.p$d f", "weird.pdf"),
    ],
)
def test_safe_filename_non_ascii_keeps_extension(name, expected):
    """Previously '請求書.pdf' became 'pdf' (no extension)."""
    assert attachments.safe_filename(name) == expected


def test_save_non_ascii_pdf_keeps_extension(isolated_sandbox):
    assert attachments.save_to_sandbox(b"%PDF", "請求書.pdf").suffix == ".pdf"


def test_save_to_sandbox_creates_dir_and_never_clobbers(isolated_sandbox):
    assert not isolated_sandbox.exists()
    p1 = attachments.save_to_sandbox(b"one", "photo.jpg")
    p2 = attachments.save_to_sandbox(b"two", "photo.jpg")
    p3 = attachments.save_to_sandbox(b"three", "../photo.jpg")
    assert p1 == isolated_sandbox / "photo.jpg"
    assert p2 == isolated_sandbox / "photo_1.jpg"
    assert p3 == isolated_sandbox / "photo_2.jpg"
    assert [p.read_bytes() for p in (p1, p2, p3)] == [b"one", b"two", b"three"]
    for p in (p1, p2, p3):
        assert p.parent == isolated_sandbox


def test_save_to_sandbox_no_extension(isolated_sandbox):
    attachments.save_to_sandbox(b"a", "README")
    assert attachments.save_to_sandbox(b"b", "README") == isolated_sandbox / "README_1"

