"""CSRF origin guard: the decision, the ASGI wiring, and the config plumbing.

``latticeai/core/csrf.py`` is the only thing standing between a
cookie-authenticated deployment and a forged cross-site write, so every test
here is written so that removing or inverting the guard fails it. The three
layers are exercised separately because they can break independently:

* ``normalize_origin`` / ``CSRFOriginPolicy.evaluate`` — pure decisions,
  asserted on *both* ``allowed`` and ``reason`` so a permissive fallthrough
  cannot masquerade as a deliberate exemption.
* ``CSRFOriginGuardMiddleware`` — the real ASGI path, driven through
  ``TestClient`` so header decoding and the short-circuit response are covered.
* ``Config`` / ``build_web_runtime`` — the plumbing that decides which origins
  the shipped app actually trusts, and in what middleware order.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, List, MutableMapping, Tuple

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from latticeai.core.config import Config
from latticeai.core.csrf import (
    CSRFDecision,
    CSRFOriginGuardMiddleware,
    CSRFOriginPolicy,
    normalize_origin,
)
from latticeai.runtime.web_runtime import build_web_runtime

HOSTILE_ORIGIN = "https://evil.example"
SESSION_COOKIE = "session_token=abc123"


def _policy(**overrides: Any) -> CSRFOriginPolicy:
    settings: Dict[str, Any] = {
        "trusted_origins": (),
        "server_host": "127.0.0.1",
        "server_port": 4825,
        "bind_is_loopback": True,
    }
    settings.update(overrides)
    return CSRFOriginPolicy(**settings)


def _decide(policy: CSRFOriginPolicy, **overrides: Any) -> CSRFDecision:
    """Evaluate a forged cross-site cookie POST unless the test says otherwise.

    The baseline is *denied*, so any test asserting ``allowed`` is asserting a
    real difference rather than inheriting a permissive default.
    """
    request: Dict[str, Any] = {
        "method": "POST",
        "origin": HOSTILE_ORIGIN,
        "referer": None,
        "host": "127.0.0.1:4825",
        "cookie_header": SESSION_COOKIE,
        "authorization": None,
    }
    request.update(overrides)
    return policy.evaluate(**request)


def test_the_shared_baseline_request_is_denied():
    # Guards the helper itself: if this ever passes, every "allowed" assertion
    # below becomes vacuous.
    assert _decide(_policy()) == CSRFDecision(False, "cross-site-origin")


# ── normalize_origin ───────────────────────────────────────────────────────

def test_normalize_origin_lowercases_scheme_and_host_and_drops_path():
    assert normalize_origin("HTTP://Localhost:4825/some/path?q=1") == ("http", "localhost", 4825)
    assert normalize_origin("  HTTPS://APP.Example.COM  ") == ("https", "app.example.com", None)
    assert normalize_origin("https://app.example.com/") == normalize_origin("HTTPS://App.Example.com")


def test_normalize_origin_collapses_only_the_scheme_default_port():
    assert normalize_origin("http://x.example:80") == normalize_origin("http://x.example")
    assert normalize_origin("https://x.example:443") == normalize_origin("https://x.example")
    assert normalize_origin("ws://x.example:80") == ("ws", "x.example", None)
    assert normalize_origin("wss://x.example:443") == ("wss", "x.example", None)
    # A non-default port is part of the identity: :443 over http is not :80.
    assert normalize_origin("http://x.example:443") == ("http", "x.example", 443)
    assert normalize_origin("https://x.example:80") == ("https", "x.example", 80)
    # No scheme means there is no default to collapse against.
    assert normalize_origin("x.example:80") == ("", "x.example", 80)


def test_normalize_origin_reads_a_bare_authority_like_a_host_header():
    assert normalize_origin("pub.example") == ("", "pub.example", None)
    assert normalize_origin("pub.example:4825") == ("", "pub.example", 4825)
    assert normalize_origin("[::1]:4825") == ("", "::1", 4825)


@pytest.mark.parametrize("opaque", ["null", "NULL", "Null", "  null  "])
def test_normalize_origin_refuses_the_opaque_null_origin(opaque):
    # "null" is what a sandboxed iframe / file:// page sends. Normalizing it
    # into a matchable value would make every opaque origin equal to itself.
    assert normalize_origin(opaque) is None


@pytest.mark.parametrize("malformed", ["http://x.example:notaport", "x.example:99999999", "http://x.example:-1"])
def test_normalize_origin_refuses_a_malformed_port(malformed):
    assert normalize_origin(malformed) is None


@pytest.mark.parametrize("unusable", [None, "", "   ", "http://", "//", "/only/a/path"])
def test_normalize_origin_refuses_values_with_no_host(unusable):
    assert normalize_origin(unusable) is None


# ── CSRFOriginPolicy.evaluate ──────────────────────────────────────────────

@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS", "TRACE", "get", "options"])
def test_safe_methods_are_allowed_even_when_everything_else_is_hostile(method):
    policy = _policy()
    assert _decide(policy, method=method) == CSRFDecision(True, "safe-method")
    # The method is what flips it: the same headers on a write are refused.
    assert _decide(policy, method="POST") == CSRFDecision(False, "cross-site-origin")


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "post"])
def test_every_state_changing_method_is_guarded(method):
    assert _decide(_policy(), method=method) == CSRFDecision(False, "cross-site-origin")


def test_bearer_authenticated_write_is_exempt_even_from_a_hostile_origin():
    policy = _policy()
    # A cross-site page cannot attach Authorization without a preflight the
    # CORS allowlist rejects, so this credential is not ambient.
    assert _decide(policy, authorization="Bearer session-abc") == CSRFDecision(True, "bearer-auth")
    assert _decide(policy, authorization="  bearer session-abc  ") == CSRFDecision(True, "bearer-auth")
    # Anything that is not the bearer scheme is not an exemption.
    assert _decide(policy, authorization="Basic dXNlcjpwdw==") == CSRFDecision(False, "cross-site-origin")
    assert _decide(policy, authorization="Bearer") == CSRFDecision(False, "cross-site-origin")
    assert _decide(policy, authorization="") == CSRFDecision(False, "cross-site-origin")


def test_a_request_without_the_session_cookie_is_not_this_guards_business():
    policy = _policy()
    assert _decide(policy, cookie_header=None) == CSRFDecision(True, "no-session-cookie")
    assert _decide(policy, cookie_header="") == CSRFDecision(True, "no-session-cookie")


@pytest.mark.parametrize(
    "jar",
    ["theme=dark", "lt_invite=abc; theme=dark", "not_session_token=abc", "session_token_extra=abc"],
)
def test_other_cookies_do_not_count_as_a_session_credential(jar):
    # A cookie jar that cannot authenticate the request must not be treated as
    # one, or every visitor carrying an unrelated cookie gets a 403.
    assert _decide(_policy(), cookie_header=jar) == CSRFDecision(True, "no-session-cookie")


@pytest.mark.parametrize(
    "jar",
    [
        "=broken; session_token=abc",
        "garbage; session_token=abc",
        'bad="unterminated; session_token=abc',
        " session_token = abc ",
        "session_token",
        "session_token=abc; garbage",
    ],
)
def test_a_malformed_cookie_pair_cannot_hide_the_session_cookie(jar):
    # Parsed by hand rather than via http.cookies precisely so one broken pair
    # cannot make the jar look empty and buy an attacker the exemption above.
    assert _decide(_policy(), cookie_header=jar) == CSRFDecision(False, "cross-site-origin")


def test_cross_site_origin_with_a_session_cookie_is_denied():
    policy = _policy(trusted_origins=["https://app.example"])
    for origin in (
        "https://evil.example",
        "https://app.example.evil.test",  # suffix that merely ends with the name
        "https://evil.app.example",       # sibling label
        "https://app.example:8443",       # same host, different port
        "http://app.example",             # scheme downgrade, both sides stated
    ):
        assert _decide(policy, origin=origin) == CSRFDecision(False, "cross-site-origin"), origin


def test_an_opaque_null_origin_with_a_session_cookie_is_denied():
    policy = _policy(trusted_origins=["https://app.example"], bind_is_loopback=True)
    assert _decide(policy, origin="null") == CSRFDecision(False, "opaque-origin")
    # A stated-but-unusable Origin is opaque too, not "absent".
    assert _decide(policy, origin="http://") == CSRFDecision(False, "opaque-origin")
    # "null" must not fall through to the Referer fallback or the loopback
    # exemption — both of which would allow this exact request.
    assert _decide(policy, origin="null", referer="https://app.example/x") == CSRFDecision(False, "opaque-origin")
    assert _decide(policy, origin=None, referer=None) == CSRFDecision(True, "no-origin-loopback-bind")


def test_a_headerless_client_is_trusted_only_while_the_bind_is_loopback():
    loopback = _policy(bind_is_loopback=True)
    reachable = _policy(bind_is_loopback=False, server_host="0.0.0.0")
    assert _decide(loopback, origin=None, referer=None) == CSRFDecision(True, "no-origin-loopback-bind")
    assert _decide(reachable, origin=None, referer=None) == CSRFDecision(False, "no-origin-reachable-bind")
    # Empty header values are "absent", not "opaque".
    assert _decide(reachable, origin="", referer="") == CSRFDecision(False, "no-origin-reachable-bind")
    assert _decide(loopback, origin="", referer="") == CSRFDecision(True, "no-origin-loopback-bind")


def test_referer_is_the_fallback_when_the_client_omits_origin():
    policy = _policy(trusted_origins=["https://app.example"], bind_is_loopback=True)
    assert _decide(policy, origin=None, referer="https://app.example/settings?tab=1") == CSRFDecision(
        True, "same-site-or-trusted-origin"
    )
    # A hostile Referer must not fall through to the loopback exemption.
    assert _decide(policy, origin=None, referer="https://evil.example/attack") == CSRFDecision(
        False, "cross-site-origin"
    )
    # Origin wins whenever both are present; Referer cannot launder it.
    assert _decide(policy, origin="https://evil.example", referer="https://app.example/ok") == CSRFDecision(
        False, "cross-site-origin"
    )


def test_origin_matching_the_requests_own_host_is_same_site_without_configuration():
    # Reverse proxy: the public hostname is not the bind address and was never
    # listed, but only a page this server served can produce Origin == Host.
    policy = _policy(trusted_origins=())
    assert _decide(policy, origin="https://pub.example", host="pub.example") == CSRFDecision(
        True, "same-site-or-trusted-origin"
    )
    assert _decide(policy, origin="http://pub.example:8080", host="pub.example:8080") == CSRFDecision(
        True, "same-site-or-trusted-origin"
    )
    # The Host has to actually match — it is not a blanket exemption.
    assert _decide(policy, origin="https://evil.example", host="pub.example") == CSRFDecision(
        False, "cross-site-origin"
    )
    assert _decide(policy, origin="https://pub.example", host=None) == CSRFDecision(False, "cross-site-origin")


def test_tls_terminating_proxy_scheme_mismatch_still_reads_as_same_site():
    # The Host header carries no scheme, and a TLS-terminating proxy speaks
    # http to us while the browser reports https. Comparing the authority is
    # what keeps that deployment working.
    policy = _policy()
    assert _decide(policy, origin="https://pub.example", host="pub.example") == CSRFDecision(
        True, "same-site-or-trusted-origin"
    )
    assert _decide(policy, origin="http://pub.example", host="pub.example") == CSRFDecision(
        True, "same-site-or-trusted-origin"
    )
    # Ignoring the scheme does not mean ignoring the port.
    assert _decide(policy, origin="https://pub.example:8443", host="pub.example") == CSRFDecision(
        False, "cross-site-origin"
    )


def test_explicitly_configured_trusted_origin_is_allowed():
    policy = _policy(trusted_origins=["https://pub.example", "http://lan.box:4825"])
    for origin in ("https://pub.example", "https://pub.example:443", "https://PUB.Example/", "http://lan.box:4825"):
        assert _decide(policy, origin=origin) == CSRFDecision(True, "same-site-or-trusted-origin"), origin
    # Everything outside the list stays cross-site.
    assert _decide(policy, origin="https://other.example") == CSRFDecision(False, "cross-site-origin")
    assert _decide(policy, origin="http://lan.box:4826") == CSRFDecision(False, "cross-site-origin")
    # Unusable entries are dropped rather than widening the allowlist.
    assert normalize_origin("null") not in _policy(trusted_origins=["null", ""]).trusted_origins


def test_the_servers_own_origin_and_loopback_are_trusted_by_default():
    policy = _policy(server_host="192.168.0.10", server_port=9000, bind_is_loopback=False)
    for origin in (
        "http://192.168.0.10:9000",
        "https://192.168.0.10:9000",
        "http://localhost:9000",
        "http://127.0.0.1:9000",
        "http://[::1]:9000",
    ):
        assert _decide(policy, origin=origin, host="192.168.0.10:9000") == CSRFDecision(
            True, "same-site-or-trusted-origin"
        ), origin
    # A different port on the same host is a different origin.
    assert _decide(policy, origin="http://localhost:9001", host="192.168.0.10:9000") == CSRFDecision(
        False, "cross-site-origin"
    )


# ── CSRFOriginGuardMiddleware (real ASGI) ──────────────────────────────────

def _with_write_route(app: FastAPI) -> Tuple[TestClient, List[str]]:
    reached: List[str] = []

    @app.post("/write")
    async def write() -> Dict[str, str]:
        reached.append("write")
        return {"ok": "written"}

    return TestClient(app), reached


def _guarded_client(policy: CSRFOriginPolicy) -> Tuple[TestClient, List[str]]:
    app = FastAPI()
    app.add_middleware(CSRFOriginGuardMiddleware, policy=policy)
    return _with_write_route(app)


def test_middleware_rejects_a_forged_cross_site_cookie_post():
    client, reached = _guarded_client(_policy(trusted_origins=["https://app.example"]))

    response = client.post("/write", headers={"Origin": HOSTILE_ORIGIN, "Cookie": SESSION_COOKIE})

    assert response.status_code == 403
    body = response.json()
    assert body["error"] == "csrf_origin_rejected"
    assert body["reason"] == "cross-site-origin"
    assert body["detail"], "a rejected user needs a human-readable reason"
    assert response.headers["content-type"] == "application/json; charset=utf-8"
    assert reached == [], "the guard must refuse before the route body runs"


def test_middleware_lets_a_trusted_cookie_post_reach_the_route():
    client, reached = _guarded_client(_policy(trusted_origins=["https://app.example"]))

    response = client.post("/write", headers={"Origin": "https://app.example", "Cookie": SESSION_COOKIE})

    assert response.status_code == 200
    assert response.json() == {"ok": "written"}
    assert reached == ["write"]


def test_middleware_reads_the_hosts_own_origin_out_of_the_raw_scope_headers():
    # TestClient's base_url makes Host "testserver"; nothing lists it, so this
    # only passes if the middleware decodes and forwards the Host header.
    client, reached = _guarded_client(_policy())

    response = client.post("/write", headers={"Origin": "http://testserver", "Cookie": SESSION_COOKIE})

    assert response.status_code == 200
    assert reached == ["write"]


def test_middleware_denies_a_headerless_cookie_post_on_a_reachable_bind():
    reachable_client, reachable_reached = _guarded_client(_policy(bind_is_loopback=False, server_host="0.0.0.0"))
    denied = reachable_client.post("/write", headers={"Cookie": SESSION_COOKIE})
    assert denied.status_code == 403
    assert denied.json()["reason"] == "no-origin-reachable-bind"
    assert reachable_reached == []

    # The identical request is the local CLI when the bind is loopback.
    loopback_client, loopback_reached = _guarded_client(_policy(bind_is_loopback=True))
    assert loopback_client.post("/write", headers={"Cookie": SESSION_COOKIE}).status_code == 200
    assert loopback_reached == ["write"]


def test_middleware_leaves_safe_and_uncredentialed_requests_alone():
    client, reached = _guarded_client(_policy(bind_is_loopback=False, server_host="0.0.0.0"))

    assert client.get("/write", headers={"Origin": HOSTILE_ORIGIN, "Cookie": SESSION_COOKIE}).status_code == 405
    assert client.post("/write", headers={"Origin": HOSTILE_ORIGIN}).status_code == 200
    assert reached == ["write"]


def test_middleware_passes_non_http_scopes_through_untouched():
    seen: List[str] = []
    sent: List[MutableMapping[str, Any]] = []

    async def inner(
        scope: MutableMapping[str, Any],
        receive: Callable[[], Any],
        send: Callable[[MutableMapping[str, Any]], Any],
    ) -> None:
        seen.append(str(scope.get("type")))

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(message)

    async def receive() -> MutableMapping[str, Any]:
        return {"type": "lifespan.startup"}

    # A bind that denies everything it is allowed to judge, so a pass-through
    # failure would be visible rather than accidentally permissive.
    guard = CSRFOriginGuardMiddleware(inner, policy=_policy(bind_is_loopback=False, server_host="0.0.0.0"))
    for scope in ({"type": "lifespan"}, {"type": "lifespan", "asgi": {"version": "3.0"}}):
        asyncio.run(guard(scope, receive, send))

    assert seen == ["lifespan", "lifespan"], "lifespan must reach the wrapped app"
    assert sent == [], "the guard must never answer a non-http scope"


# ── Config plumbing ────────────────────────────────────────────────────────

def test_csrf_trusted_origins_parse_into_a_list():
    cfg = Config.from_env({"LATTICEAI_CSRF_TRUSTED_ORIGINS": "https://a.example, https://b.example"})
    assert cfg.csrf_trusted_origins == ["https://a.example", "https://b.example"]
    assert Config.from_env(
        {"LATTICEAI_CSRF_TRUSTED_ORIGINS": " https://a.example ,https://b.example ,"}
    ).csrf_trusted_origins == ["https://a.example", "https://b.example"]


def test_csrf_trusted_origins_default_to_empty_and_stay_separate_from_cors():
    assert Config.from_env({}).csrf_trusted_origins == []
    assert Config.from_env({"LATTICEAI_CSRF_TRUSTED_ORIGINS": ""}).csrf_trusted_origins == []
    assert Config.from_env({"LATTICEAI_CSRF_TRUSTED_ORIGINS": " , ,"}).csrf_trusted_origins == []
    # Two independent allowlists: a CORS entry is not silently a CSRF entry
    # here, and a CSRF entry must not widen CORS.
    cfg = Config.from_env(
        {
            "LATTICEAI_CORS_ALLOWED_ORIGINS": "https://cors.example",
            "LATTICEAI_CSRF_TRUSTED_ORIGINS": "https://proxied.example",
        }
    )
    assert cfg.cors_extra_origins == ["https://cors.example"]
    assert cfg.csrf_trusted_origins == ["https://proxied.example"]


def test_csrf_trusted_origins_read_the_process_environment(monkeypatch):
    monkeypatch.setenv("LATTICEAI_CSRF_TRUSTED_ORIGINS", "https://proxied.example")
    assert Config.from_env().csrf_trusted_origins == ["https://proxied.example"]
    monkeypatch.delenv("LATTICEAI_CSRF_TRUSTED_ORIGINS")
    assert Config.from_env().csrf_trusted_origins == []


# ── build_web_runtime wiring ───────────────────────────────────────────────

@pytest.fixture
def web_runtime(tmp_path: Path) -> Callable[..., Dict[str, Any]]:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield

    def build(**overrides: Any) -> Dict[str, Any]:
        settings: Dict[str, Any] = {
            "app_mode": "local",
            "app_version": "test",
            "lifespan": lifespan,
            "default_host": "127.0.0.1",
            "default_port": 4825,
            "cors_extra_origins": [],
            "cors_allow_network": False,
            "static_dir": tmp_path / "static",
            "csrf_trusted_origins": [],
        }
        settings.update(overrides)
        return build_web_runtime(**settings)

    return build


def test_build_web_runtime_merges_cors_and_extra_csrf_origins(web_runtime):
    runtime = web_runtime(
        cors_extra_origins=["https://cors.example"],
        csrf_trusted_origins=["https://proxied.example"],
    )

    cors = runtime["CORS_ALLOWED_ORIGINS"]
    assert cors == ["http://localhost:4825", "http://127.0.0.1:4825", "https://cors.example"]
    assert runtime["CSRF_ALLOWED_ORIGINS"] == [*cors, "https://proxied.example"]
    # The reverse-proxy escape hatch must not silently widen CORS.
    assert "https://proxied.example" not in cors


def test_the_installed_guard_trusts_exactly_the_merged_origin_list(web_runtime):
    runtime = web_runtime(
        cors_extra_origins=["https://cors.example"],
        csrf_trusted_origins=["https://proxied.example"],
    )
    guard = next(m for m in runtime["app"].user_middleware if m.cls is CSRFOriginGuardMiddleware)
    trusted = guard.kwargs["policy"].trusted_origins

    for origin in runtime["CSRF_ALLOWED_ORIGINS"]:
        assert normalize_origin(origin) in trusted, origin
    assert normalize_origin("https://evil.example") not in trusted


def test_cors_middleware_is_installed_outside_the_csrf_guard(web_runtime):
    app = web_runtime()["app"]

    declared = [entry.cls for entry in app.user_middleware]
    assert CORSMiddleware in declared and CSRFOriginGuardMiddleware in declared
    # user_middleware is outermost-first (add_middleware inserts at 0), so CORS
    # being added last must place it ahead of the guard.
    assert declared.index(CORSMiddleware) < declared.index(CSRFOriginGuardMiddleware)

    # And the same order must survive into the instantiated stack.
    nesting: List[str] = []
    node: Any = app.build_middleware_stack()
    while node is not None and len(nesting) < 20:
        nesting.append(type(node).__name__)
        node = getattr(node, "app", None)
    assert nesting.index("CORSMiddleware") < nesting.index("CSRFOriginGuardMiddleware")


def test_the_built_app_admits_both_origin_lists_and_refuses_everything_else(web_runtime):
    runtime = web_runtime(
        cors_extra_origins=["https://cors.example"],
        csrf_trusted_origins=["https://proxied.example"],
    )
    client, reached = _with_write_route(runtime["app"])

    allowed = ("https://cors.example", "https://proxied.example", "http://127.0.0.1:4825", "http://localhost:4825")
    for origin in allowed:
        response = client.post("/write", headers={"Origin": origin, "Cookie": SESSION_COOKIE})
        assert response.status_code == 200, origin

    forged = client.post("/write", headers={"Origin": HOSTILE_ORIGIN, "Cookie": SESSION_COOKIE})
    assert forged.status_code == 403
    assert forged.json()["error"] == "csrf_origin_rejected"
    assert reached == ["write"] * len(allowed)


def test_the_guards_bind_posture_follows_the_configured_host(web_runtime):
    loopback_client, loopback_reached = _with_write_route(web_runtime(default_host="127.0.0.1")["app"])
    assert loopback_client.post("/write", headers={"Cookie": SESSION_COOKIE}).status_code == 200
    assert loopback_reached == ["write"]

    reachable_client, reachable_reached = _with_write_route(
        web_runtime(default_host="0.0.0.0", cors_allow_network=True)["app"]
    )
    denied = reachable_client.post("/write", headers={"Cookie": SESSION_COOKIE})
    assert denied.status_code == 403
    assert denied.json()["reason"] == "no-origin-reachable-bind"
    assert reachable_reached == []
