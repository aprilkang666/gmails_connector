"""Outlook (Microsoft Graph) backend. httpx.request is replaced with a fake
router returning canned Graph JSON; tokens are stubbed."""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from gmail_mcp import outlook


class FakeGraph:
    """Records requests and answers them from a {(method, path): json} map."""

    def __init__(self):
        self.routes: dict[tuple[str, str], object] = {}
        self.calls: list[dict] = []

    def __call__(self, method, url, headers=None, timeout=None, **kwargs):
        path = url.removeprefix(outlook.GRAPH)
        self.calls.append({"method": method, "path": path, "headers": headers, **kwargs})
        key = (method, path)
        if key not in self.routes:
            return httpx.Response(404, text=f"no fake route for {key}")
        body = self.routes[key]
        if isinstance(body, httpx.Response):
            return body
        return httpx.Response(200, json=body)


@pytest.fixture
def graph(monkeypatch):
    fake = FakeGraph()
    monkeypatch.setattr(httpx, "request", fake)
    monkeypatch.setattr(outlook, "_token", lambda alias: "TEST-TOKEN")
    return fake


def addr(email, name=""):
    return {"emailAddress": {"address": email, "name": name}}


# --- search: query quoting (1c78e62) ----------------------------------------


@pytest.mark.parametrize(
    "query,expected",
    [
        ("from:bob@x.com", '"from:bob@x.com"'),
        ('subject:"quarterly review"', '"subject: quarterly review"'),
        ('"exact phrase"', '"exact phrase"'),
        ('  padded  ', '"padded"'),
    ],
)
def test_search_strips_inner_quotes(graph, query, expected):
    graph.routes[("GET", "/me/messages")] = {"value": []}
    outlook.search("work", query, 7)
    params = graph.calls[0]["params"]
    assert params["$search"] == expected
    assert params["$search"].count('"') == 2
    assert params["$top"] == "7"
    assert graph.calls[0]["headers"]["Authorization"] == "Bearer TEST-TOKEN"


def test_search_no_results_echoes_original_query(graph):
    graph.routes[("GET", "/me/messages")] = {"value": []}
    assert outlook.search("work", 'a "b"') == "No messages matched 'a \"b\"' in work."


def test_search_formats_results(graph):
    graph.routes[("GET", "/me/messages")] = {
        "value": [
            {
                "id": "AAA",
                "conversationId": "CONV1",
                "subject": "Invoice",
                "from": addr("bob@x.com", "Bob"),
                "receivedDateTime": "2026-01-02T03:04:05Z",
                "bodyPreview": "y" * 200,
            },
            {"id": "BBB", "from": addr("c@x.com", "c@x.com"), "bodyPreview": None},
        ]
    }
    out = outlook.search("work", "invoice")
    assert out.startswith("Found 2 messages in work for: invoice")
    assert "[AAA] 2026-01-02T03:04:05Z" in out
    assert "From: Bob <bob@x.com>" in out
    assert "Thread: CONV1" in out
    assert "y" * 120 in out and "y" * 121 not in out
    # name equal to address collapses to the bare address
    assert "From: c@x.com\n" in out


def test_http_error_raises_runtime_error(graph):
    graph.routes[("GET", "/me/messages")] = httpx.Response(400, text="Syntax error")
    with pytest.raises(RuntimeError, match=r"failed \(400\): Syntax error"):
        outlook.search("work", "x")


# --- message formatting -----------------------------------------------------


def test_fmt_helpers():
    assert outlook._fmt_addr(None) == ""
    assert outlook._fmt_addr(addr("a@x.com")) == "a@x.com"
    assert outlook._fmt_addr(addr("a@x.com", "A")) == "A <a@x.com>"
    assert outlook._fmt_recipients([addr("a@x.com"), addr("b@x.com", "B")]) == "a@x.com, B <b@x.com>"
    assert outlook._recipient_list(" a@x.com, ,b@y.com ") == [
        {"emailAddress": {"address": "a@x.com"}},
        {"emailAddress": {"address": "b@y.com"}},
    ]


def test_read_message(graph):
    graph.routes[("GET", "/me/messages/M1")] = {
        "subject": "Hi",
        "from": addr("bob@x.com", "Bob"),
        "toRecipients": [addr("me@work.com")],
        "ccRecipients": [],
        "receivedDateTime": "2026-01-01T00:00:00Z",
        "body": {"contentType": "text", "content": "Body text"},
    }
    out = outlook.read_message("work", "M1")
    assert out == (
        "From: Bob <bob@x.com>\nTo: me@work.com\nCc: \n"
        "Date: 2026-01-01T00:00:00Z\nSubject: Hi\n\nBody text"
    )
    assert graph.calls[0]["headers"]["Prefer"] == 'outlook.body-content-type="text"'


def test_read_thread(graph):
    graph.routes[("GET", "/me/messages")] = {
        "value": [
            {"id": "1", "subject": "S", "from": addr("a@x.com"), "body": {"content": "one"}},
            {"id": "2", "subject": "Re: S", "from": addr("b@x.com"), "body": None},
        ]
    }
    out = outlook.read_thread("work", "CONV")
    assert graph.calls[0]["params"]["$filter"] == "conversationId eq 'CONV'"
    assert out.startswith("Thread CONV (2 messages):")
    assert "--- Message 1 [1] ---" in out and "one" in out
    assert "--- Message 2 [2] ---" in out and "(no readable body)" in out


def test_create_draft_payload(graph):
    graph.routes[("POST", "/me/messages")] = {"id": "DRAFT1"}
    out = outlook.create_draft("work", "a@x.com, b@x.com", "Subj", "Body", cc="c@x.com")
    sent = json.loads(graph.calls[0]["content"])
    assert sent["toRecipients"] == [
        {"emailAddress": {"address": "a@x.com"}},
        {"emailAddress": {"address": "b@x.com"}},
    ]
    assert sent["ccRecipients"] == [{"emailAddress": {"address": "c@x.com"}}]
    assert "bccRecipients" not in sent
    assert "Draft ID: DRAFT1" in out


def test_modify_labels_unread_and_categories(graph):
    graph.routes[("GET", "/me/messages/M1")] = {"categories": ["Old", "Keep"]}
    graph.routes[("PATCH", "/me/messages/M1")] = {}
    out = outlook.modify_labels("work", "M1", add=["UNREAD", "New"], remove=["Old"])
    patch = json.loads(graph.calls[-1]["content"])
    assert patch == {"isRead": False, "categories": ["Keep", "New"]}
    assert "marked unread" in out


def test_modify_labels_noop(graph):
    assert outlook.modify_labels("work", "M1").startswith("No changes requested")
    assert graph.calls == []


# --- attachments ------------------------------------------------------------

ATTS = {
    "value": [
        {"id": "A0", "name": "Report.pdf", "contentType": "application/pdf", "size": 1234},
        {"id": "A1", "name": "notes.txt", "contentType": "text/plain", "size": 5},
        {"id": "A2", "name": "Fwd: meeting", "contentType": None, "size": 0},
    ]
}


def test_list_attachments(graph):
    graph.routes[("GET", "/me/messages/M1/attachments")] = ATTS
    out = outlook.list_attachments("work", "M1")
    assert "[0] Report.pdf" in out and "Type: application/pdf  Size: 1234 bytes" in out
    assert "[1] notes.txt" in out


def test_list_attachments_none(graph):
    graph.routes[("GET", "/me/messages/M1/attachments")] = {"value": []}
    assert outlook.list_attachments("work", "M1").startswith("No attachments")


@pytest.mark.parametrize("ident,att_id", [("1", "A1"), ("notes.txt", "A1"), ("report.PDF", "A0")])
def test_get_attachment(graph, ident, att_id):
    graph.routes[("GET", "/me/messages/M1/attachments")] = ATTS
    graph.routes[("GET", f"/me/messages/M1/attachments/{att_id}")] = {
        "contentBytes": base64.b64encode(b"payload").decode()
    }
    name, mime, data = outlook.get_attachment("work", "M1", ident)
    assert data == b"payload"
    assert graph.calls[-1]["path"] == f"/me/messages/M1/attachments/{att_id}"


def test_get_attachment_item_attachment_has_no_bytes(graph):
    graph.routes[("GET", "/me/messages/M1/attachments")] = ATTS
    graph.routes[("GET", "/me/messages/M1/attachments/A2")] = {
        "@odata.type": "#microsoft.graph.itemAttachment"
    }
    with pytest.raises(ValueError, match="itemAttachment and has no downloadable bytes"):
        outlook.get_attachment("work", "M1", "2")


def test_get_attachment_unknown(graph):
    graph.routes[("GET", "/me/messages/M1/attachments")] = ATTS
    with pytest.raises(ValueError, match=r"Available: \[0\] Report.pdf"):
        outlook.get_attachment("work", "M1", "missing.doc")
