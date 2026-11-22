"""Config normalisation / alias resolution. File-path lookup is deliberately
not tested here (it's being reworked separately); load_config is monkeypatched."""

from __future__ import annotations

import pytest

from gmail_mcp import config
from gmail_mcp.config import ConfigError, _normalize_account


def test_string_form_defaults_to_gmail():
    assert _normalize_account("p", "a@gmail.com") == {
        "email": "a@gmail.com",
        "provider": "gmail",
    }


def test_object_form_with_provider():
    assert _normalize_account("w", {"email": "a@x.com", "provider": "outlook"}) == {
        "email": "a@x.com",
        "provider": "outlook",
    }


def test_object_form_without_provider_defaults_to_gmail():
    assert _normalize_account("w", {"email": "a@x.com"})["provider"] == "gmail"


@pytest.mark.parametrize("provider", ["yahoo", "Outlook", "", None])
def test_unknown_provider_rejected(provider):
    with pytest.raises(ConfigError, match="unknown provider"):
        _normalize_account("w", {"email": "a@x.com", "provider": provider})


@pytest.mark.parametrize("value", [{}, {"email": ""}, {"email": 123}, {"provider": "gmail"}])
def test_missing_or_bad_email_rejected(value):
    with pytest.raises(ConfigError, match="string 'email' field"):
        _normalize_account("w", value)


@pytest.mark.parametrize("value", [None, 42, ["a@x.com"]])
def test_wrong_type_rejected(value):
    with pytest.raises(ConfigError, match="must be an email string"):
        _normalize_account("w", value)


def test_resolve_account_and_helpers(fake_config):
    assert config.resolve_account("work") == {"email": "me@work.com", "provider": "outlook"}
    assert config.resolve_alias("personal") == "me@gmail.com"
    assert config.get_provider("personal") == "gmail"
    assert config.get_provider("work") == "outlook"
    assert config.list_account_aliases() == ["personal", "work", "side"]


def test_unknown_alias_lists_available(fake_config):
    with pytest.raises(ConfigError, match="Unknown account alias 'nope'") as exc:
        config.resolve_account("nope")
    assert "personal" in str(exc.value) and "work" in str(exc.value)


def test_supported_providers():
    assert set(config.SUPPORTED_PROVIDERS) == {"gmail", "outlook"}
