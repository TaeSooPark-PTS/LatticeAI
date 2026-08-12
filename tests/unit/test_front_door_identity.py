"""v11.5.2 — what the worker knows about the front door in front of it.

The Rust gateway is a reverse proxy on a second loopback port. Three facts that
used to be lost on the hop, and are now stated and (conditionally) believed:

* **Who the caller reached.** ``Host`` is hop-by-hop, so the worker saw its own
  internal authority and the CSRF guard's ``Origin == Host`` fallback rejected
  every cookie-authenticated write as ``csrf_origin_rejected``.
* **What the worker's access posture is.** ``GET /health`` states it, so the
  gateway can gate its own credential-free ``/rust/*`` lanes on the same rule
  Python applies (``trusted_local_owner``) instead of assuming it.
* **Which origins may call cross-origin.** The supervisor injects the gateway's
  origins into ``LATTICEAI_CORS_ALLOWED_ORIGINS``; this asserts the Python half
  of that contract, so the two sides cannot drift.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from latticeai.api.auth import create_auth_router
from latticeai.api.health import create_health_router
from latticeai.api.permissions import PermissionGateway
from latticeai.api.static_routes import PRODUCTION_CSP
from latticeai.core.config import Config, default_sso_redirect_uri
from latticeai.core.csrf import CSRFOriginGuardMiddleware, CSRFOriginPolicy
from latticeai.core.http_origin import (
    FORWARDED_HOST_HEADER,
    FORWARDED_PROTO_HEADER,
    effective_host,
    effective_origin,
    peer_may_forward,
    request_external_origin,
)
from latticeai.core.security import configure_trusted_proxies
from latticeai.runtime.access_runtime import is_externally_reachable
from latticeai.runtime.web_runtime import build_web_runtime

# ── http_origin: who may forward, and what is believed ───────────────────────


@pytest.fixture(autouse=True)
def _no_trusted_proxies():
    """The product default: nothing off-loopback may forward anything."""
    configure_trusted_proxies([])
    yield
    configure_trusted_proxies([])


def test_only_loopback_and_listed_proxies_may_forward():
    assert peer_may_forward("127.0.0.1")
    assert peer_may_forward("::1")
    assert peer_may_forward(" 127.0.0.1 "), "an ASGI server may pad the value"
    assert not peer_may_forward("203.0.113.7")
    assert not peer_may_forward(None), "no peer, no trust"
    assert not peer_may_forward(""), "no peer, no trust"
    assert not peer_may_forward("not-an-address")

    assert configure_trusted_proxies(["203.0.113.0/24"]) == 1
    assert peer_may_forward("203.0.113.7"), "the operator listed this edge"
    assert not peer_may_forward("198.51.100.7")


def test_the_forwarded_host_is_believed_only_from_a_peer_that_may_forward():
    # The desktop topology: the gateway forwards over loopback.
    assert (
        effective_host(
            host="127.0.0.1:4899",
            forwarded_host="127.0.0.1:4825",
            peer="127.0.0.1",
        )
        == "127.0.0.1:4825"
    )
    # A comma-joined chain names the client's own hop first.
    assert (
        effective_host(
            host="127.0.0.1:4899",
            forwarded_host="localhost:4825, inner",
            peer="127.0.0.1",
        )
        == "localhost:4825"
    )
    # Off-loopback and unlisted: the claim is discarded, the real Host stands.
    assert (
        effective_host(
            host="lattice.internal",
            forwarded_host="evil.example",
            peer="203.0.113.7",
        )
        == "lattice.internal"
    )
    # A believable peer that forwards nothing usable changes nothing.
    for blank in (None, "", "  ", " , "):
        assert (
            effective_host(host="127.0.0.1:4899", forwarded_host=blank, peer="127.0.0.1")
            == "127.0.0.1:4899"
        )
    # No proxy at all is the direct topology, unchanged.
    assert effective_host(host="127.0.0.1:4825") == "127.0.0.1:4825"
    # Nothing to report is reported as nothing, not as a guess.
    assert effective_host(host=None) is None
    assert effective_host(host="   ") is None
    assert effective_host(host=None, forwarded_host="x", peer="203.0.113.7") is None


def test_the_effective_origin_carries_the_scheme_the_browser_used():
    assert (
        effective_origin(host="127.0.0.1:4899", forwarded_host="127.0.0.1:4825", peer="127.0.0.1")
        == "http://127.0.0.1:4825"
    )
    # A TLS-terminating proxy speaks http to us and https to the browser.
    assert (
        effective_origin(
            host="127.0.0.1:4899",
            forwarded_host="lattice.example",
            forwarded_proto="HTTPS",
            peer="127.0.0.1",
        )
        == "https://lattice.example"
    )
    # A scheme we do not recognise is not taken on trust.
    assert (
        effective_origin(
            host="lattice.example",
            forwarded_proto="gopher",
            peer="127.0.0.1",
        )
        == "http://lattice.example"
    )
    # …and neither is one from a peer that may not forward.
    assert (
        effective_origin(
            host="lattice.example",
            forwarded_proto="https",
            peer="203.0.113.7",
        )
        == "http://lattice.example"
    )
    # The server's own scheme is the default when nothing is forwarded.
    assert effective_origin(host="lattice.example", scheme="https") == "https://lattice.example"
    assert effective_origin(host="lattice.example", scheme="") == "http://lattice.example"
    assert effective_origin(host=None) is None


# ── csrf: the Origin == Host fallback through a proxy hop ────────────────────


def _policy() -> CSRFOriginPolicy:
    """The worker's own policy: it knows only the port it bound."""
    return CSRFOriginPolicy(server_host="127.0.0.1", server_port=4899, bind_is_loopback=True)


def _write(**kwargs: Any):
    return _policy().evaluate(
        method="PATCH",
        origin=kwargs.pop("origin", "http://127.0.0.1:4825"),
        referer=None,
        host=kwargs.pop("host", "127.0.0.1:4899"),
        cookie_header="session_token=abc",
        authorization=None,
        **kwargs,
    )


def test_a_proxied_write_is_same_site_by_the_forwarded_host():
    # Without the forwarded fact this is the live 403 the audit reproduced.
    refused = _write()
    assert not refused.allowed
    assert refused.reason == "cross-site-origin"

    allowed = _write(forwarded_host="127.0.0.1:4825", peer="127.0.0.1")
    assert allowed.allowed
    assert allowed.reason == "same-site-or-trusted-origin"


def test_a_forwarded_host_from_an_untrusted_peer_changes_nothing():
    refused = _write(
        origin="http://evil.example",
        forwarded_host="evil.example",
        peer="203.0.113.7",
    )
    assert not refused.allowed, "an off-loopback peer cannot rename the front door"

    # Loopback *can*, which is the point — and is no wider than the `Origin`
    # a local non-browser caller could already have forged.
    allowed = _write(
        origin="http://evil.example",
        forwarded_host="evil.example",
        peer="127.0.0.1",
    )
    assert allowed.allowed


def test_an_origin_already_in_the_trust_set_never_consults_the_proxy():
    decision = _write(origin="http://127.0.0.1:4899", host="anything")
    assert decision.allowed
    assert decision.reason == "same-site-or-trusted-origin"


def _guarded_app() -> FastAPI:
    app = FastAPI()

    @app.patch("/api/thing")
    async def thing():
        return {"ok": True}

    app.add_middleware(CSRFOriginGuardMiddleware, policy=_policy())
    return app


def test_the_middleware_reads_the_forwarded_host_and_the_peer():
    headers = {
        "Origin": "http://127.0.0.1:4825",
        "Host": "127.0.0.1:4899",
        "Cookie": "session_token=abc",
    }
    # The peer the gateway connects from: loopback, because it is on this
    # machine. (TestClient's default peer is the literal "testclient", which is
    # not an address and is therefore not believed — asserted below.)
    proxied = TestClient(_guarded_app(), client=("127.0.0.1", 50000))
    assert proxied.patch("/api/thing", headers=headers).status_code == 403
    allowed = proxied.patch(
        "/api/thing",
        headers={**headers, FORWARDED_HOST_HEADER: "127.0.0.1:4825"},
    )
    assert allowed.status_code == 200

    unnameable = TestClient(_guarded_app())
    refused = unnameable.patch(
        "/api/thing",
        headers={**headers, FORWARDED_HOST_HEADER: "127.0.0.1:4825"},
    )
    assert refused.status_code == 403, "a peer that is not an address is not trusted"


def test_a_scope_without_a_client_still_decides():
    """An ASGI transport that models no socket must not crash the guard."""
    sent: List[Dict[str, Any]] = []

    async def _send(message):
        sent.append(message)

    async def _receive():  # pragma: no cover - never reached: the guard refuses
        return {"type": "http.request"}

    async def _app(scope, receive, send):  # pragma: no cover - refused first
        raise AssertionError("the guard must refuse before the app is called")

    import asyncio

    guard = CSRFOriginGuardMiddleware(_app, policy=_policy())
    asyncio.run(
        guard(
            {
                "type": "http",
                "method": "PATCH",
                "client": None,
                "headers": [
                    (b"origin", b"http://127.0.0.1:4825"),
                    (b"host", b"127.0.0.1:4899"),
                    (b"cookie", b"session_token=abc"),
                    (FORWARDED_HOST_HEADER.encode(), b"127.0.0.1:4825"),
                ],
            },
            _receive,
            _send,
        )
    )
    assert sent[0]["status"] == 403, "no peer means the forwarded host is not believed"


# ── /health states the posture the native lanes are gated on ─────────────────


class _Service:
    def health_base(self, *, version: str, mode: str) -> Dict[str, Any]:
        return {"status": "ok", "version": version, "mode": mode}

    def health_full(self, base: Dict[str, Any], engines: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {**base, "engines": engines}


def _health_client(*, require_auth: bool, externally_reachable: bool) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_health_router(
            model_service=_Service(),
            engine_status=lambda: [],
            get_current_user=lambda request: None,
            require_auth=require_auth,
            externally_reachable=externally_reachable,
            app_version="11.5.2",
            app_mode="local",
        )
    )
    return TestClient(app)


@pytest.mark.parametrize(
    ("require_auth", "reachable", "open_posture"),
    [
        (False, False, True),
        (True, False, False),
        (False, True, False),
        (True, True, False),
    ],
)
def test_health_states_both_halves_of_trusted_local_owner(
    require_auth: bool, reachable: bool, open_posture: bool
):
    access = _health_client(
        require_auth=require_auth, externally_reachable=reachable
    ).get("/health").json()["access"]
    assert access == {"require_auth": require_auth, "externally_reachable": reachable}
    # The rule the Rust gateway applies to the same two fields.
    assert (not access["require_auth"] and not access["externally_reachable"]) is open_posture


def test_the_reachability_fact_has_one_definition():
    class _Cfg:
        is_public = False
        network_exposed = False

    cfg = _Cfg()
    assert not is_externally_reachable(cfg)
    cfg.network_exposed = True
    assert is_externally_reachable(cfg)
    cfg.network_exposed = False
    cfg.is_public = True
    assert is_externally_reachable(cfg)
    assert not is_externally_reachable(object()), "an object stating neither is local"


# ── the gateway's origins arrive as CORS origins ─────────────────────────────


def test_the_injected_gateway_origins_become_cors_and_csrf_origins(tmp_path):
    """The Python half of the supervisor's ``LATTICEAI_CORS_ALLOWED_ORIGINS``.

    ``rust/lattice-host/src/supervisor/worker_env.rs`` writes exactly this
    string for a worker behind a gateway; a preflight from the gateway origin
    used to come back with no ``Access-Control-Allow-Origin`` at all.
    """
    injected = "http://127.0.0.1:4825,http://localhost:4825,http://[::1]:4825"
    config = Config.from_env(
        {
            "LATTICEAI_PORT": "4899",
            "LATTICEAI_CORS_ALLOWED_ORIGINS": injected,
            "LATTICEAI_DATA_DIR": str(tmp_path),
        }
    )
    assert config.cors_extra_origins == injected.split(",")

    runtime = build_web_runtime(
        app_mode="local",
        app_version="11.5.2",
        lifespan=None,
        default_host=config.host,
        default_port=config.port,
        cors_extra_origins=config.cors_extra_origins,
        cors_allow_network=config.cors_allow_network,
        static_dir=tmp_path / "static",
        csrf_trusted_origins=config.csrf_trusted_origins,
    )
    for origin in injected.split(","):
        assert origin in runtime["CORS_ALLOWED_ORIGINS"], origin
        # Anything CORS lets send credentials is, by that decision, trusted
        # with the session cookie.
        assert origin in runtime["CSRF_ALLOWED_ORIGINS"], origin
    # Additive: the worker's own origin is still there.
    assert "http://127.0.0.1:4899" in runtime["CORS_ALLOWED_ORIGINS"]


def test_the_production_csp_names_no_websocket_scheme():
    """There is no WebSocket endpoint in this product; SSE is fetch-based."""
    assert "ws://" not in PRODUCTION_CSP
    assert "wss://" not in PRODUCTION_CSP
    assert "connect-src 'self' http://127.0.0.1:*;" in PRODUCTION_CSP
    assert FORWARDED_PROTO_HEADER == "x-forwarded-proto"


# ── absolute-URL producers: the links the front door has to appear in ────────


class _Peer:
    def __init__(self, host: str) -> None:
        self.host = host


class _Url:
    def __init__(self, scheme: str) -> None:
        self.scheme = scheme


class _LinkRequest:
    """The three attributes ``request_external_origin`` reads."""

    def __init__(self, *, host: str, peer: str, scheme: str = "http", **forwarded: str) -> None:
        self.headers = {"host": host, **forwarded}
        self.client = _Peer(peer)
        self.url = _Url(scheme)


def test_a_link_names_the_gateway_when_a_loopback_proxy_says_so():
    origin = request_external_origin(
        _LinkRequest(
            host="127.0.0.1:4899",
            peer="127.0.0.1",
            **{FORWARDED_HOST_HEADER: "127.0.0.1:4825", FORWARDED_PROTO_HEADER: "http"},
        )
    )

    assert origin == "http://127.0.0.1:4825"


def test_a_direct_link_is_unchanged_by_the_new_rule():
    # No proxy in front: the answer is exactly what it always was.
    assert request_external_origin(
        _LinkRequest(host="lattice.example", peer="127.0.0.1")
    ) == "http://lattice.example"


def test_a_link_ignores_a_forwarded_host_from_an_untrusted_peer():
    assert request_external_origin(
        _LinkRequest(
            host="lattice.example",
            peer="203.0.113.7",
            **{FORWARDED_HOST_HEADER: "attacker.example"},
        )
    ) == "http://lattice.example"


def test_a_request_that_states_no_authority_falls_back():
    class _Bare:
        headers: Dict[str, str] = {}
        client = None
        url = _Url("http")

    assert request_external_origin(_Bare(), fallback="http://localhost:4825") == (
        "http://localhost:4825"
    )
    assert request_external_origin(_Bare()) is None


def _permission_gateway(tmp_path, port: int = 4899):
    return PermissionGateway(
        config=SimpleNamespace(
            discord_permission_webhook="",
            discord_bot_token="",
            discord_permission_channel="",
            permission_monitor_secret="",
            port=port,
        ),
        data_dir=tmp_path / "perm",
        require_admin=lambda _request: ("admin@example.com", {}),
        get_current_user=lambda _request: "owner@example.com",
    )


def test_the_permission_ui_url_prefers_configuration_then_the_front_door(tmp_path, monkeypatch):
    """Precedence, stated once: operator config → front door → loopback."""
    monkeypatch.delenv("LATTICEAI_PERMISSION_UI_URL", raising=False)
    monkeypatch.delenv("LATTICEAI_PUBLIC_URL", raising=False)
    gateway = _permission_gateway(tmp_path)

    # Nothing configured, nothing observed: the worker's own address.
    assert gateway.permission_ui_url == "http://127.0.0.1:4899/app#/admin/permissions"

    # A local approval arrives through the gateway — the approver must be sent
    # to the door they can actually open.
    gateway.remember_front_door(
        _LinkRequest(
            host="127.0.0.1:4899",
            peer="127.0.0.1",
            **{FORWARDED_HOST_HEADER: "127.0.0.1:4825"},
        )
    )
    assert gateway.permission_ui_url == "http://127.0.0.1:4825/app#/admin/permissions"

    # An operator statement outranks anything observed.
    gateway._public_url = "https://lattice.example"
    assert gateway.permission_ui_url == "https://lattice.example/app#/admin/permissions"
    gateway._explicit_ui_url = "https://ops.example/approve"
    assert gateway.permission_ui_url == "https://ops.example/approve"


def _sso_authorize_redirect_uri(*, redirect_uri: str, default_redirect_uri: str, peer: str):
    """Drive the real ``/auth/sso/login`` route and read back what it sent."""
    settings = {
        "enabled": True,
        "client_id": "client-1",
        "client_secret": "secret",
        "redirect_uri": redirect_uri,
        "scopes": "openid email",
    }

    async def _discovery():
        return {"authorization_endpoint": "https://idp.example/authorize"}

    router = create_auth_router(
        load_users=dict,
        save_users=lambda _users: None,
        hash_password=lambda password: password,
        verify_and_migrate=lambda *a, **k: True,
        create_session=lambda _email: "tok",
        get_session_email=lambda _token: None,
        invalidate_session=lambda _token: None,
        extract_bearer_token=lambda _request: None,
        get_user_role=lambda *a, **k: "user",
        require_user=lambda _request: "owner@example.com",
        check_ip_rate_limit=lambda *a, **k: None,
        client_ip=lambda _request: "127.0.0.1",
        get_sso_settings=lambda: dict(settings),
        get_sso_discovery=_discovery,
        public_sso_config=lambda *a, **k: {},
        open_registration=False,
        session_ttl=60,
        default_redirect_uri=default_redirect_uri,
    )
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app, client=(peer, 51000))
    response = client.get(
        "/auth/sso/login",
        headers={"host": "127.0.0.1:4899", FORWARDED_HOST_HEADER: "127.0.0.1:4825"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 307), response.text
    query = parse_qs(urlparse(response.headers["location"]).query)
    return query["redirect_uri"][0]


def test_the_sso_redirect_substitutes_the_front_door_only_for_the_default():
    """A registered redirect URI is sent verbatim; the built-in default is not.

    The default names the worker's own port, so behind the gateway it is a
    callback the browser can never reach — while a URI the operator configured
    is registered with the identity provider and must not be rewritten.
    """
    default = default_sso_redirect_uri(4899)

    assert _sso_authorize_redirect_uri(
        redirect_uri=default, default_redirect_uri=default, peer="127.0.0.1",
    ) == "http://127.0.0.1:4825/auth/sso/callback"

    assert _sso_authorize_redirect_uri(
        redirect_uri="https://lattice.example/auth/sso/callback",
        default_redirect_uri=default,
        peer="127.0.0.1",
    ) == "https://lattice.example/auth/sso/callback"

    # An untrusted peer cannot move the callback to a host of its choosing.
    assert _sso_authorize_redirect_uri(
        redirect_uri=default, default_redirect_uri=default, peer="203.0.113.7",
    ) == "http://127.0.0.1:4899/auth/sso/callback"
