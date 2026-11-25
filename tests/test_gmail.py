"""Gmail backend: MIME decoding, search formatting, attachments.

The Gmail discovery client is replaced with a MagicMock whose chained calls
(svc.users().messages().get(...).execute()) return canned API dicts.
"""

from __future__ import annotations

import base64
from unittest.mock import MagicMock

import pytest

from gmail_mcp import gmail
from tests.conftest import b64url

LONG_PLAIN = "Hello Bob, here is the real plain-text body of the message. " * 2
HTML = "<html><body><p>Real <b>HTML</b> body</p></body></html>"


def part(mime, data=None, parts=None, filename="", attachment_id=None, size=0):
    body = {}
    if data is not None:
        body["data"] = b64url(data)
    if attachment_id:
        body["attachmentId"] = attachment_id
        body["size"] = size
    p = {"mimeType": mime, "filename": filename, "body": body}
    if parts is not None:
        p["parts"] = parts
    return p


# --- _decode_body -----------------------------------------------------------


def test_decode_single_plain_part():
    assert gmail._decode_body(part("text/plain", LONG_PLAIN)) == LONG_PLAIN


def test_decode_prefers_plain_in_alternative():
    payload = part(
        "multipart/alternative",
        parts=[part("text/plain", LONG_PLAIN), part("text/html", HTML)],
    )
    assert gmail._decode_body(payload) == LONG_PLAIN


@pytest.mark.parametrize("placeholder", [" ", "\r\n", "View in browser"])
def test_decode_skips_placeholder_plain_part(placeholder):
    """Regression for 571f6dc: a tiny text/plain placeholder next to a real
    text/html body must not win."""
    payload = part(
        "multipart/alternative",
        parts=[part("text/plain", placeholder), part("text/html", HTML)],
    )
    out = gmail._decode_body(payload)
    assert "Real HTML body" in out
    assert "<" not in out


def test_decode_nested_multipart():
    payload = part(
        "multipart/mixed",
        parts=[
            part(
                "multipart/alternative",
                parts=[part("text/plain", ""), part("text/html", HTML)],
            ),
            part("application/pdf", filename="a.pdf", attachment_id="A1", size=10),
        ],
    )
    assert "Real HTML body" in gmail._decode_body(payload)


def test_decode_non_ascii_utf8():
    text = "日本語のメール本文です。" * 10
    assert gmail._decode_body(part("text/plain", text)) == text


def test_decode_no_body():
    payload = part("multipart/mixed", parts=[part("image/png", filename="x.png")])
    assert gmail._decode_body(payload) == "(no readable body)"


@pytest.mark.xfail(
    reason=(
        "BUG (from 571f6dc): MIN_PLAIN_CHARS=50 also drops genuinely short "
        "plain-text-only emails (e.g. 'Thanks!') when there is no HTML "
        "alternative; the user gets '(no readable body)' instead of the text."
    ),
    strict=True,
)
def test_decode_short_plain_only_message_is_kept():
    assert gmail._decode_body(part("text/plain", "Thanks!")) == "Thanks!"


# --- fake service -----------------------------------------------------------


@pytest.fixture
def svc(monkeypatch):
    service = MagicMock(name="gmail-service")
    monkeypatch.setattr(gmail, "_service", lambda alias: service)
    return service


def _headers(**kw):
    return [{"name": k, "value": v} for k, v in kw.items()]


def test_header_lookup_case_insensitive():
    h = _headers(Subject="Hi")
    assert gmail._header(h, "subject") == "Hi"
    assert gmail._header(h, "From") == ""


def test_search_formats_results(svc):
    msgs = svc.users.return_value.messages.return_value
    msgs.list.return_value.execute.return_value = {"messages": [{"id": "m1"}, {"id": "m2"}]}
    metas = {
        "m1": {
            "payload": {"headers": _headers(From="a@x.com", Subject="One", Date="Mon")},
            "snippet": "x" * 300,
        },
        "m2": {
            "payload": {"headers": _headers(From="b@x.com", Subject="Two", Date="Tue")},
            "snippet": "short",
        },
    }
    msgs.get.side_effect = lambda **kw: MagicMock(execute=lambda: metas[kw["id"]])

    out = gmail.search("personal", "is:unread", 5)

    msgs.list.assert_called_once_with(userId="me", q="is:unread", maxResults=5)
    assert out.startswith("Found 2 messages in personal for: is:unread")
    assert "[m1] Mon" in out and "From: a@x.com" in out and "Subj: One" in out
    assert "[m2] Tue" in out and "Snippet: short" in out
    # snippets are truncated to 120 chars
    assert "x" * 120 in out and "x" * 121 not in out


def test_search_no_results(svc):
    svc.users.return_value.messages.return_value.list.return_value.execute.return_value = {}
    assert gmail.search("personal", "nothing") == "No messages matched 'nothing' in personal."


def test_read_message(svc):
    svc.users.return_value.messages.return_value.get.return_value.execute.return_value = {
        "payload": {
            "headers": _headers(From="a@x.com", To="me@gmail.com", Date="Mon", Subject="Hi"),
            **part("text/plain", LONG_PLAIN),
        }
    }
    out = gmail.read_message("personal", "m1")
    assert "From: a@x.com\nTo: me@gmail.com\nDate: Mon\nSubject: Hi\n\n" in out
    assert out.endswith(LONG_PLAIN)


# --- attachments ------------------------------------------------------------

ATT_PAYLOAD = part(
    "multipart/mixed",
    parts=[
        part("text/plain", LONG_PLAIN),
        part("application/pdf", filename="Report.pdf", attachment_id="ATT0", size=1234),
        part(
            "multipart/related",
            parts=[part("image/png", filename="logo.png", attachment_id="ATT1", size=99)],
        ),
        # inline part with attachmentId but no filename is not an attachment
        part("image/gif", attachment_id="INLINE"),
    ],
)


def test_walk_attachment_parts():
    atts = gmail._walk_attachment_parts(ATT_PAYLOAD)
    assert atts == [
        {"filename": "Report.pdf", "mime_type": "application/pdf", "attachment_id": "ATT0", "size": 1234},
        {"filename": "logo.png", "mime_type": "image/png", "attachment_id": "ATT1", "size": 99},
    ]


def test_list_attachments(svc):
    svc.users.return_value.messages.return_value.get.return_value.execute.return_value = {
        "payload": ATT_PAYLOAD
    }
    out = gmail.list_attachments("personal", "m1")
    assert "[0] Report.pdf" in out and "Type: application/pdf  Size: 1234 bytes" in out
    assert "[1] logo.png" in out


def test_list_attachments_none(svc):
    svc.users.return_value.messages.return_value.get.return_value.execute.return_value = {
        "payload": part("text/plain", LONG_PLAIN)
    }
    assert gmail.list_attachments("personal", "m1").startswith("No attachments")


@pytest.mark.parametrize("ident,expected_id", [("0", "ATT0"), ("1", "ATT1"), ("logo.png", "ATT1"), ("REPORT.PDF", "ATT0")])
def test_get_attachment_by_index_or_name(svc, ident, expected_id):
    msgs = svc.users.return_value.messages.return_value
    msgs.get.return_value.execute.return_value = {"payload": ATT_PAYLOAD}
    msgs.attachments.return_value.get.return_value.execute.return_value = {
        "data": base64.urlsafe_b64encode(b"\x00\x01bytes").decode()
    }
    name, mime, data = gmail.get_attachment("personal", "m1", ident)
    assert data == b"\x00\x01bytes"
    msgs.attachments.return_value.get.assert_called_once_with(
        userId="me", messageId="m1", id=expected_id
    )
    assert name in ("Report.pdf", "logo.png")


def test_get_attachment_unknown_lists_available(svc):
    svc.users.return_value.messages.return_value.get.return_value.execute.return_value = {
        "payload": ATT_PAYLOAD
    }
    with pytest.raises(ValueError, match=r"Available: \[0\] Report.pdf, \[1\] logo.png"):
        gmail.get_attachment("personal", "m1", "nope.txt")
