from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from latticeai.core.messages import (
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
    def __init__(self, **headers: str) -> None:
        self.headers = {
            key.replace("_", "-").lower(): value for key, value in headers.items()
        }


def test_every_message_exists_in_every_language():
    missing = [
        f"{key}:{language}"
        for key, entry in MESSAGES.items()
        for language in SUPPORTED_LANGUAGES
        if not entry.get(language)
    ]
    assert missing == [], f"messages missing a translation: {missing}"


def test_no_message_is_the_same_string_in_both_languages():
    identical = [key for key, entry in MESSAGES.items() if entry["ko"] == entry["en"]]
    assert identical == []


def test_korean_entries_actually_contain_korean():
    hangul = re.compile(r"[가-힣]")
    assert [
        key for key, entry in MESSAGES.items() if not hangul.search(entry["ko"])
    ] == []


def test_english_entries_contain_no_korean():
    hangul = re.compile(r"[가-힣]")
    assert [key for key, entry in MESSAGES.items() if hangul.search(entry["en"])] == []


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


def test_unknown_key_returns_the_key_rather_than_raising():
    assert translate("nope.not.a.key", "en") == "nope.not.a.key"


def test_http_error_carries_the_localized_detail():
    ko = http_error(404, "auth.user_not_found", "ko")
    en = http_error(404, "auth.user_not_found", "en")
    assert ko.status_code == en.status_code == 404
    assert ko.detail == MESSAGES["auth.user_not_found"]["ko"]
    assert en.detail == MESSAGES["auth.user_not_found"]["en"]
    assert ko.detail != en.detail


MIGRATED_ROUTERS = [
    "latticeai/api/agent_worker_seam.py",
    "latticeai/api/health.py",
    "latticeai/api/models.py",
    "latticeai/api/search.py",
    "latticeai/api/worker_compute.py",
    "latticeai/api/worker_seams.py",
]


def _raises_own_wording(source: str) -> bool:
    for line in source.splitlines():
        match = re.search(r"detail\s*=\s*(.+)$", line)
        if not match:
            continue
        value = match.group(1).strip()
        if value.startswith("str("):
            continue
        return True
    return False


def test_the_two_gates_agree_on_which_routers_are_migrated():
    gate = (REPO / "scripts/check_server_i18n.mjs").read_text(encoding="utf-8")
    body = gate.split("const LOCALIZED = [", 1)[1].split("]", 1)[0]
    from_gate = {
        line.strip().strip(',"')
        for line in body.splitlines()
        if line.strip().startswith('"')
    }
    from_tests = {
        path.rsplit("/", 1)[1].removesuffix(".py") for path in MIGRATED_ROUTERS
    }
    assert from_gate == from_tests, (
        "scripts/check_server_i18n.mjs and MIGRATED_ROUTERS disagree: "
        f"only in gate={sorted(from_gate - from_tests)}, "
        f"only in tests={sorted(from_tests - from_gate)}"
    )


@pytest.mark.parametrize("relative", MIGRATED_ROUTERS)
def test_migrated_routers_raise_no_hardcoded_korean_detail(relative):
    source = (REPO / relative).read_text(encoding="utf-8")
    literals = re.findall(r'detail="[^"]*[가-힣][^"]*"', source)
    assert literals == [], (
        f"{relative} still answers in one fixed language: {literals}. "
        "Add the message to latticeai/core/messages.py and raise http_error()."
    )


@pytest.mark.parametrize("relative", MIGRATED_ROUTERS)
def test_migrated_routers_take_their_wording_from_the_catalog(relative):
    source = (REPO / relative).read_text(encoding="utf-8")
    if not _raises_own_wording(source):
        return
    assert "http_error(" in source or "translate(" in source, (
        f"{relative} is listed as migrated but never reads the catalog"
    )


@pytest.mark.parametrize("relative", MIGRATED_ROUTERS)
def test_migrated_routers_never_fix_the_language_at_the_raise_site(relative):
    source = (REPO / relative).read_text(encoding="utf-8")
    if not _raises_own_wording(source):
        return
    resolves = "resolve_language(" in source
    receives = "language: str" in source or "language=language" in source
    assert resolves or receives, (
        f"{relative} localizes without ever learning which language to use"
    )


def test_every_key_the_routers_reference_exists_in_the_catalog():
    referenced: set[str] = set()
    for relative in MIGRATED_ROUTERS:
        source = (REPO / relative).read_text(encoding="utf-8")
        referenced.update(re.findall(r'http_error\(\s*\d+\s*,\s*"([^"]+)"', source))
    assert referenced, "no message keys found — the extraction regex has drifted"
    assert sorted(referenced - set(MESSAGES)) == []


def _seam_client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from latticeai.api.agent_worker_seam import (
        SEAM_ENV_VAR,
        create_agent_worker_seam_router,
    )

    monkeypatch.delenv(SEAM_ENV_VAR, raising=False)
    app = FastAPI()
    app.include_router(
        create_agent_worker_seam_router(
            model_router=object(),
            dispatch_service=object(),
            execute_tool=lambda name, args: {},
            hooks=None,
            require_user=lambda _request: "owner@example.com",
            enforce_rate_limit=lambda *_args: None,
        )
    )
    return TestClient(app)


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_a_closed_seam_is_explained_in_the_requested_language(language, monkeypatch):
    response = _seam_client(monkeypatch).post(
        "/agent/tool",
        json={"tool": "read_file", "args": {"path": "a.md"}},
        headers={LANGUAGE_HEADER: language},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == MESSAGES["agent_seam.disabled"][language]


def test_a_browser_that_only_sends_accept_language_is_still_understood(monkeypatch):
    response = _seam_client(monkeypatch).post(
        "/agent/tool",
        json={"tool": "read_file", "args": {"path": "a.md"}},
        headers={"Accept-Language": "en-US,en;q=0.9"},
    )
    assert response.json()["detail"] == MESSAGES["agent_seam.disabled"]["en"]


def test_the_compute_seam_can_resolve_a_language_too(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from latticeai.api.agent_worker_seam import SEAM_ENV_VAR
    from latticeai.api.worker_seams import create_worker_seams_router

    monkeypatch.delenv(SEAM_ENV_VAR, raising=False)
    app = FastAPI()
    app.include_router(
        create_worker_seams_router(
            model_router=None,
            require_user=lambda _request: "owner@example.com",
            enforce_rate_limit=lambda *_args: None,
        )
    )
    response = TestClient(app).get(
        "/worker/sysinfo",
        headers={LANGUAGE_HEADER: "en"},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == MESSAGES["agent_seam.disabled"]["en"]
