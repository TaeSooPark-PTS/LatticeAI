"""Server-side message catalog: parity, resolution, and end-to-end wiring.

The catalog exists because the API used to answer in whichever language the
endpoint's author was thinking in — Korean from `auth.py`, English from
`browser.py` — so every user read half the product in a language they had not
chosen. These tests hold that line: both languages for every message, a
language resolved from the request rather than the call site, and no endpoint
quietly reverting to a hardcoded literal.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from latticeai.core.messages import (  # noqa: E402
    DEFAULT_LANGUAGE,
    LANGUAGE_HEADER,
    MESSAGES,
    SUPPORTED_LANGUAGES,
    http_error,
    resolve_language,
    translate,
)

REPO = Path(__file__).resolve().parents[2]


class _Request:
    """Minimal stand-in: `resolve_language` only ever reads `.headers`."""

    def __init__(self, **headers: str) -> None:
        self.headers = {key.replace("_", "-").lower(): value for key, value in headers.items()}


# ── catalog integrity ───────────────────────────────────────────────────


def test_every_message_exists_in_every_language():
    missing = [
        f"{key}:{language}"
        for key, entry in MESSAGES.items()
        for language in SUPPORTED_LANGUAGES
        if not entry.get(language)
    ]
    assert missing == [], f"messages missing a translation: {missing}"


def test_no_message_is_the_same_string_in_both_languages():
    """A copy-pasted entry is an untranslated one wearing a translation's clothes."""
    identical = [
        key for key, entry in MESSAGES.items()
        if entry["ko"] == entry["en"]
    ]
    assert identical == []


def test_korean_entries_actually_contain_korean():
    hangul = re.compile(r"[가-힣]")
    assert [key for key, entry in MESSAGES.items() if not hangul.search(entry["ko"])] == []


def test_english_entries_contain_no_korean():
    hangul = re.compile(r"[가-힣]")
    assert [key for key, entry in MESSAGES.items() if hangul.search(entry["en"])] == []


# ── language resolution ─────────────────────────────────────────────────


def test_explicit_header_wins_over_browser_preference():
    request = _Request(**{LANGUAGE_HEADER: "en", "accept-language": "ko-KR,ko;q=0.9"})
    assert resolve_language(request) == "en"


def test_accept_language_is_used_when_the_product_sent_nothing():
    assert resolve_language(_Request(accept_language="en-GB,en;q=0.9,ko;q=0.8")) == "en"
    assert resolve_language(_Request(accept_language="ko-KR,ko;q=0.9")) == "ko"


def test_unsupported_languages_fall_through_to_a_supported_one():
    assert resolve_language(_Request(accept_language="fr-FR,fr;q=0.9,en;q=0.8")) == "en"


def test_resolution_never_returns_an_unsupported_language():
    for header in ("", "  ", "xx", "*", "zh-CN", "en_US", None):
        request = _Request() if header is None else _Request(accept_language=header)
        assert resolve_language(request) in SUPPORTED_LANGUAGES


def test_resolution_survives_a_request_without_headers():
    class Bare:
        pass

    assert resolve_language(Bare()) == DEFAULT_LANGUAGE


# ── translation ─────────────────────────────────────────────────────────


def test_unknown_key_returns_the_key_rather_than_raising():
    # A missing message must never turn a 404 into a 500, and the namespaced
    # key is something a bug report can be written about.
    assert translate("nope.not.a.key", "en") == "nope.not.a.key"


def test_http_error_carries_the_localized_detail():
    ko = http_error(404, "auth.user_not_found", "ko")
    en = http_error(404, "auth.user_not_found", "en")
    assert ko.status_code == en.status_code == 404
    assert ko.detail == MESSAGES["auth.user_not_found"]["ko"]
    assert en.detail == MESSAGES["auth.user_not_found"]["en"]
    assert ko.detail != en.detail


# ── the routers actually use it ─────────────────────────────────────────

MIGRATED_ROUTERS = [
    "latticeai/api/auth.py",
    "latticeai/api/admin.py",
    "latticeai/api/browser.py",
]


@pytest.mark.parametrize("relative", MIGRATED_ROUTERS)
def test_migrated_routers_raise_no_hardcoded_korean_detail(relative):
    source = (REPO / relative).read_text(encoding="utf-8")
    literals = re.findall(r'detail="[^"]*[가-힣][^"]*"', source)
    assert literals == [], (
        f"{relative} still answers in one fixed language: {literals}. "
        "Add the message to latticeai/core/messages.py and raise http_error()."
    )


@pytest.mark.parametrize("relative", MIGRATED_ROUTERS)
def test_migrated_routers_resolve_the_language_from_the_request(relative):
    source = (REPO / relative).read_text(encoding="utf-8")
    assert "resolve_language(" in source
    assert "http_error(" in source


def test_every_key_the_routers_reference_exists_in_the_catalog():
    """A typo'd key renders as the key itself — caught here instead of in the UI."""
    referenced: set[str] = set()
    for relative in MIGRATED_ROUTERS:
        source = (REPO / relative).read_text(encoding="utf-8")
        referenced.update(re.findall(r'http_error\(\s*\d+\s*,\s*"([^"]+)"', source))
    assert referenced, "no message keys found — the extraction regex has drifted"
    assert sorted(referenced - set(MESSAGES)) == []


# ── end to end: a real request gets a real localized answer ─────────────


def _auth_client():
    """The auth router wired to in-memory stubs, so a 401/400 can be observed."""
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.testclient import TestClient

    from latticeai.api.auth import create_auth_router

    def require_user(_request: Request) -> str:
        raise HTTPException(status_code=401, detail="auth required")

    app = FastAPI()
    app.include_router(create_auth_router(
        load_users=lambda: {},
        save_users=lambda _users: None,
        hash_password=lambda value: f"hashed:{value}",
        verify_and_migrate=lambda *_args: True,
        create_session=lambda email: f"session:{email}",
        get_session_email=lambda _token: None,
        invalidate_session=lambda _token: None,
        extract_bearer_token=lambda _request: None,
        get_user_role=lambda _email, _users=None: "user",
        require_user=require_user,
        check_ip_rate_limit=lambda *_args, **_kwargs: None,
        client_ip=lambda _request: "127.0.0.1",
        get_sso_settings=lambda: {},
        get_sso_discovery=lambda _settings: None,
        public_sso_config=lambda **_kwargs: {},
        open_registration=True,
        session_ttl=3600,
        require_auth=True,
    ))
    return TestClient(app)


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_rejected_password_is_explained_in_the_requested_language(language):
    response = _auth_client().post(
        "/register",
        json={"email": "a@b.c", "password": "short", "name": "A", "nickname": "A"},
        headers={LANGUAGE_HEADER: language},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == MESSAGES["auth.password_too_weak"][language]


def test_a_browser_that_only_sends_accept_language_is_still_understood():
    response = _auth_client().post(
        "/register",
        json={"email": "a@b.c", "password": "short", "name": "A", "nickname": "A"},
        headers={"Accept-Language": "en-US,en;q=0.9"},
    )
    assert response.json()["detail"] == MESSAGES["auth.password_too_weak"]["en"]


def test_sso_callback_can_resolve_a_language_too():
    """It is an OAuth redirect target and had no Request parameter at all."""
    response = _auth_client().get(
        "/auth/sso/callback",
        params={"state": "never-issued"},
        headers={LANGUAGE_HEADER: "en"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == MESSAGES["sso.invalid_state"]["en"]
