"""Provider dispatch in mail.py, read_attachment routing, and cross-account ops."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from gmail_mcp import config, mail


@pytest.fixture
def backends(monkeypatch, fake_config):
    """Swap both backend modules for MagicMocks so dispatch is observable."""
    g, o = MagicMock(name="gmail"), MagicMock(name="outlook")
    monkeypatch.setitem(mail._BACKENDS, "gmail", g)
    monkeypatch.setitem(mail._BACKENDS, "outlook", o)
    return g, o


@pytest.mark.parametrize(
    "alias,expected", [("personal", "gmail"), ("side", "gmail"), ("work", "outlook")]
)
@pytest.mark.parametrize(
    "fn,args",
    [
        ("search", ("q", 5)),
        ("read_message", ("m1",)),
        ("read_thread", ("t1",)),
        ("create_draft", ("to@x.com", "S", "B", "", "")),
        ("send_draft", ("d1",)),
        ("list_labels", ()),
        ("modify_labels", ("m1", ["A"], ["B"])),
        ("list_attachments", ("m1",)),
    ],
)
def test_dispatch_by_provider(backends, alias, expected, fn, args):
    g, o = backends
    chosen, other = (g, o) if expected == "gmail" else (o, g)
    getattr(chosen, fn).return_value = "ok"
    assert getattr(mail, fn)(alias, *args) == "ok"
    getattr(chosen, fn).assert_called_once_with(alias, *args)
    getattr(other, fn).assert_not_called()


def test_dispatch_unknown_alias_raises(backends):
    with pytest.raises(config.ConfigError, match="Unknown account alias"):
        mail.search("ghost", "q")


def test_backend_unsupported_provider(monkeypatch, backends):
    monkeypatch.setattr(config, "get_provider", lambda alias: "yahoo")
    with pytest.raises(config.ConfigError, match="unsupported provider 'yahoo'"):
        mail._backend("personal")


# --- read_attachment --------------------------------------------------------


def test_read_attachment_text_inline(backends):
    g, _ = backends
    g.get_attachment.return_value = ("data.csv", "text/csv", b"a,b\n1,2\n")
    out = mail.read_attachment("personal", "m1", "0")
    assert out == "📄 data.csv (8 bytes, text)\n\na,b\n1,2\n"


def test_read_attachment_text_by_extension(backends):
    g, _ = backends
    g.get_attachment.return_value = ("x.json", "application/octet-stream", b'{"a":1}')
    assert '{"a":1}' in mail.read_attachment("personal", "m1", "0")


def test_read_attachment_pdf_by_extension(backends, monkeypatch):
    _, o = backends
    o.get_attachment.return_value = ("Scan.PDF", "application/octet-stream", b"%PDF-")
    monkeypatch.setattr(mail.attachments, "extract_pdf_text", lambda data: "EXTRACTED")
    out = mail.read_attachment("work", "m1", "Scan.PDF")
    assert out == "📄 Scan.PDF (5 bytes, PDF)\n\nEXTRACTED"


def test_read_attachment_binary_saved_to_sandbox(backends, isolated_sandbox):
    g, _ = backends
    g.get_attachment.return_value = ("../../evil photo.png", "image/png", b"\x89PNG")
    out = mail.read_attachment("personal", "m1", "0")
    saved = isolated_sandbox / "evil_photo.png"
    assert saved.read_bytes() == b"\x89PNG"
    assert f"saved to: {saved}" in out
    assert "image/png" in out


# --- cross-account ----------------------------------------------------------


@pytest.fixture
def authorized(monkeypatch):
    state = {"gmail": ["me@gmail.com", "side@gmail.com"], "outlook": ["me@work.com"]}
    monkeypatch.setattr(mail.auth, "list_authorized_emails", lambda: state["gmail"])
    monkeypatch.setattr(mail.outlook_auth, "list_authorized_emails", lambda: state["outlook"])
    return state


def test_list_all_accounts(backends, authorized):
    authorized["gmail"] = ["me@gmail.com"]
    out = mail.list_all_accounts()
    assert "personal: me@gmail.com  [gmail]  [✅ authorized]" in out
    assert "work: me@work.com  [outlook]  [✅ authorized]" in out
    assert "side: side@gmail.com  [gmail]  [❌ not authorized" in out


def test_authorization_is_per_provider(backends, authorized):
    """An email authorized under Gmail must not count as authorized for an
    Outlook account with the same address."""
    authorized["outlook"] = []
    authorized["gmail"] = ["me@gmail.com", "side@gmail.com", "me@work.com"]
    assert "work: me@work.com  [outlook]  [❌" in mail.list_all_accounts()


def test_search_all_fans_out_and_skips_unauthorized(backends, authorized):
    g, o = backends
    authorized["gmail"] = ["me@gmail.com"]
    g.search.return_value = "gmail-results"
    o.search.return_value = "outlook-results"
    out = mail.search_all("invoice", 3)
    g.search.assert_called_once_with("personal", "invoice", 3)
    o.search.assert_called_once_with("work", "invoice", 3)
    assert "=== personal (me@gmail.com) [gmail] ===\ngmail-results" in out
    assert "=== work (me@work.com) [outlook] ===\noutlook-results" in out
    assert "=== side (side@gmail.com) [gmail]: skipped (not authorized) ===" in out


def test_search_all_one_account_failing_does_not_abort(backends, authorized):
    g, o = backends
    g.search.side_effect = ["personal-ok", RuntimeError("token expired")]
    o.search.side_effect = RuntimeError("Graph API GET failed (401)")
    out = mail.search_all("x")
    assert "personal-ok" in out
    assert "=== work (me@work.com) [outlook] ===\n  Error: Graph API GET failed (401)" in out
    assert "=== side (side@gmail.com) [gmail] ===\n  Error: token expired" in out


@pytest.mark.xfail(
    reason=(
        "BUG: search_all/list_all_accounts call config.resolve_account() outside "
        "the per-account try/except, so one malformed config entry (e.g. unknown "
        "provider) aborts the whole fan-out instead of being reported per account."
    ),
    strict=True,
)
def test_search_all_malformed_account_does_not_abort(backends, authorized, fake_config):
    g, _ = backends
    g.search.return_value = "personal-ok"
    fake_config["broken"] = {"email": "x@y.com", "provider": "yahoo"}
    out = mail.search_all("x")
    assert "personal-ok" in out
    assert "broken" in out
