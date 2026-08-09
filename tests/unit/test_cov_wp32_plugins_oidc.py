"""wp32 coverage — plugin manifest/lifecycle/execution boundary and OIDC.

Both modules are refusal machines, so the tests are mostly about what they
refuse. Plugins run against a real directory in ``tmp_path`` and a recording
store port. OIDC signs real RS256 tokens with a locally generated RSA key and
verifies them against a JWKS built from that key — entirely offline, with the
clock injected so nothing depends on wall time.
"""

from __future__ import annotations

import base64
import json
import sys

import pytest

from latticeai.core import plugins as plugins_module
from latticeai.core.oidc import (
    OIDCValidationError,
    _public_key_from_jwk,
    decode_unverified_header,
    fetch_jwks,
    verify_id_token,
)
from latticeai.core.plugins import (
    PLUGIN_SDK_VERSION,
    PluginError,
    PluginExecutionResult,
    PluginRegistry,
    is_compatible,
    validate_manifest,
)

cryptography = pytest.importorskip("cryptography")
from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import padding, rsa  # noqa: E402

# ── plugins: version comparison ─────────────────────────────────────────────


def test_is_compatible_treats_unparseable_and_short_versions_as_zeroes():
    major = PLUGIN_SDK_VERSION.split(".")[0]

    assert is_compatible("{0}.x.3".format(major)) is True   # 'x' degrades to 0
    assert is_compatible(major) is True                      # padded to major.0.0
    assert is_compatible("") is True                         # no requirement
    assert is_compatible("999.0.0") is False                 # different major


# ── plugins: manifest validation ────────────────────────────────────────────


def test_validate_manifest_rejects_a_non_object():
    manifest, errors = validate_manifest(["not", "a", "manifest"])

    assert manifest is None
    assert errors == ["manifest is not a JSON object"]


def test_validate_manifest_names_every_missing_required_field():
    manifest, errors = validate_manifest({})

    assert manifest is None
    assert errors == [
        "missing required field: id",
        "missing required field: name",
        "missing required field: version",
    ]


def test_validate_manifest_rejects_non_list_permissions_and_bad_provides():
    manifest, errors = validate_manifest({
        "id": "demo",
        "name": "Demo",
        "version": "1.0.0",
        "permissions": "run_tools",
        "provides": {"skills": "hello", "nonsense": []},
    })

    assert manifest is None
    assert "permissions must be a list" in errors
    assert "provides.skills must be a list" in errors
    assert "unknown provides key: nonsense" in errors


def test_validate_manifest_rejects_a_non_object_provides():
    manifest, errors = validate_manifest({
        "id": "demo", "name": "Demo", "version": "1.0.0", "provides": ["skills"],
    })

    assert manifest is None
    assert errors == ["provides must be an object"]


def test_execution_result_serializes_for_the_api():
    result = PluginExecutionResult("demo", "run_tool", "blocked", output=None, reason="nope")

    assert result.as_dict() == {
        "plugin_id": "demo", "action": "run_tool", "status": "blocked",
        "output": None, "reason": "nope",
    }


# ── plugins: discovery ──────────────────────────────────────────────────────


def _plugin(root, plugin_id, **manifest):
    directory = root / plugin_id
    directory.mkdir(parents=True, exist_ok=True)
    payload = {"id": plugin_id, "name": plugin_id.title(), "version": "1.0.0"}
    payload.update(manifest)
    (directory / "plugin.json").write_text(
        json.dumps(payload), encoding="utf-8",
    )
    return directory


class _Store:
    def __init__(self, *, registry=None, timeline_error=None):
        self.registry = dict(registry or {})
        self.timeline: list = []
        self.timeline_error = timeline_error
        self.uninstalled: list = []
        self.enabled: list = []

    def list_plugin_registry(self):
        return self.registry

    def mark_plugin_installed(self, plugin_id, *, version, metadata):
        entry = {"installed": True, "enabled": True, "version": version, "metadata": metadata}
        self.registry[plugin_id] = entry
        return entry

    def mark_plugin_uninstalled(self, plugin_id):
        self.uninstalled.append(plugin_id)
        self.registry.pop(plugin_id, None)
        return {"status": "uninstalled", "plugin_id": plugin_id}

    def set_plugin_enabled(self, plugin_id, enabled):
        self.enabled.append((plugin_id, enabled))
        return {"plugin_id": plugin_id, "enabled": enabled}

    def record_timeline_event(self, area, event_type, payload, *, workspace_id=None):
        if self.timeline_error is not None:
            raise self.timeline_error
        self.timeline.append((area, event_type, payload, workspace_id))


def test_discover_skips_loose_files_and_directories_without_a_manifest(tmp_path):
    root = tmp_path / "plugins"
    _plugin(root, "good", provides={"skills": ["hello"]})
    (root / "bare").mkdir()
    (root / "README.md").write_text("not a plugin", encoding="utf-8")
    _plugin(root, "broken", version="not-a-version")

    discovered = PluginRegistry(root).discover()

    assert [m.id for m in discovered["valid"]] == ["good"]
    assert [entry["id"] for entry in discovered["invalid"]] == ["broken"]


# ── plugins: lifecycle ──────────────────────────────────────────────────────


def test_install_refuses_a_plugin_the_host_can_no_longer_run(tmp_path, monkeypatch):
    root = tmp_path / "plugins"
    _plugin(root, "demo")
    registry = PluginRegistry(root, store=_Store())

    # A manifest that validated at discovery time but whose requirement the
    # host answers "no" to at install time.
    monkeypatch.setattr(
        plugins_module, "is_compatible",
        lambda required, current=PLUGIN_SDK_VERSION: bool(str(required).strip()),
    )

    with pytest.raises(PluginError) as excinfo:
        registry.install("demo")

    assert "requires Lattice" in str(excinfo.value)


def test_uninstall_and_set_enabled_work_with_and_without_a_store(tmp_path):
    root = tmp_path / "plugins"
    _plugin(root, "demo")
    store = _Store()
    stored = PluginRegistry(root, store=store)
    storeless = PluginRegistry(root)

    assert stored.uninstall("demo") == {"status": "uninstalled", "plugin_id": "demo"}
    assert store.uninstalled == ["demo"]
    assert stored.set_enabled("demo", False) == {"plugin_id": "demo", "enabled": False}
    assert store.enabled == [("demo", False)]

    assert storeless.uninstall("demo") == {"status": "ok", "plugin_id": "demo"}
    assert storeless.set_enabled("demo", True) == {"plugin_id": "demo", "enabled": True}


# ── plugins: execution boundary ─────────────────────────────────────────────


def test_execute_action_reports_an_unknown_plugin_as_an_error(tmp_path):
    result = PluginRegistry(tmp_path / "plugins").execute_action("ghost", "run_tool")

    assert result.status == "error"
    assert result.reason == "plugin not found or invalid"


def test_execute_action_blocks_a_permission_that_was_never_granted(tmp_path):
    root = tmp_path / "plugins"
    _plugin(root, "demo", permissions=["run_tools"], provides={"tools": ["ping"]})
    store = _Store(registry={"demo": {"installed": True, "enabled": True, "metadata": {"permissions": []}}})

    result = PluginRegistry(root, store=store).execute_action(
        "demo", "run_tool", {"tool": "ping"}, runners={"tools": lambda **_kw: "pong"},
    )

    assert result.status == "blocked"
    assert result.reason == "permission 'run_tools' not granted at install time"


def test_execute_action_reports_a_runner_failure_as_an_error(tmp_path):
    root = tmp_path / "plugins"
    _plugin(root, "demo", permissions=["run_tools"], provides={"tools": ["ping"]})
    store = _Store(registry={
        "demo": {"installed": True, "enabled": True, "metadata": {"permissions": ["run_tools"]}},
    })

    def boom(**_kwargs):
        raise RuntimeError("the plugin exploded")

    result = PluginRegistry(root, store=store).execute_action(
        "demo", "run_tool", {"tool": "ping"}, runners={"tools": boom},
    )

    assert result.status == "error"
    assert result.reason == "the plugin exploded"
    assert [event for _area, event, _payload, _ws in store.timeline] == [
        "plugin_started", "execution_failed",
    ]


def test_execute_action_survives_a_timeline_sink_that_raises(tmp_path):
    root = tmp_path / "plugins"
    _plugin(root, "demo", permissions=["run_tools"], provides={"tools": ["ping"]})
    store = _Store(
        registry={"demo": {"installed": True, "enabled": True, "metadata": {"permissions": ["run_tools"]}}},
        timeline_error=RuntimeError("timeline is offline"),
    )

    result = PluginRegistry(root, store=store).execute_action(
        "demo", "run_tool", {"tool": "ping"},
        runners={"tools": lambda **_kw: "pong"}, workspace_id="org-1",
    )

    assert result.status == "ok"
    assert result.output == "pong"


def test_execute_action_without_a_store_skips_the_grant_check(tmp_path):
    root = tmp_path / "plugins"
    _plugin(root, "demo", permissions=["run_tools"], provides={"tools": ["ping"]})
    registry = PluginRegistry(root)

    result = registry.execute_action(
        "demo", "run_tool", {"tool": "ping"}, runners={"tools": lambda **_kw: "pong"},
    )

    assert result.status == "ok"
    assert result.output == "pong"
    # Without lifecycle persistence there is nothing that *could* have been
    # granted at install time — the grant list is empty, not implicit.
    assert registry._granted_permissions("demo") == []


# ── OIDC helpers ────────────────────────────────────────────────────────────

ISSUER = "https://idp.example.com"
AUDIENCE = "lattice-client-id"
NOW = 1_700_000_000


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _int_b64url(value: int) -> str:
    return _b64url(value.to_bytes((value.bit_length() + 7) // 8, "big"))


_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwks(kid="key-1", key=None):
    nums = (key or _KEY).public_key().public_numbers()
    return {"keys": [{
        "kty": "RSA", "kid": kid, "use": "sig", "alg": "RS256",
        "n": _int_b64url(nums.n), "e": _int_b64url(nums.e),
    }]}


def _sign(claims, *, kid="key-1", key=None, payload_bytes=None):
    header = {"alg": "RS256", "typ": "JWT"}
    if kid is not None:
        header["kid"] = kid
    body = payload_bytes if payload_bytes is not None else json.dumps(claims).encode()
    signing_input = "{0}.{1}".format(_b64url(json.dumps(header).encode()), _b64url(body))
    signature = (key or _KEY).sign(
        signing_input.encode("ascii"), padding.PKCS1v15(), hashes.SHA256(),
    )
    return "{0}.{1}".format(signing_input, _b64url(signature))


def _claims(**overrides):
    base = {
        "iss": ISSUER, "aud": AUDIENCE, "sub": "user-123",
        "email": "alice@example.com", "exp": NOW + 600, "iat": NOW, "nonce": "n-abc",
    }
    base.update(overrides)
    return base


def _verify(token, **kwargs):
    params = {
        "jwks": _jwks(), "issuer": ISSUER, "audience": AUDIENCE, "now": NOW,
    }
    params.update(kwargs)
    return verify_id_token(token, **params)


# ── OIDC: segment decoding ──────────────────────────────────────────────────


def test_an_undecodable_segment_is_rejected():
    with pytest.raises(OIDCValidationError, match="invalid base64url segment"):
        decode_unverified_header("A.payload.sig")


def test_a_jwk_whose_numbers_are_not_strings_never_becomes_a_key():
    # ``n``/``e`` present but not base64url text: the modulus decoder refuses
    # it, and the token is then rejected against every remaining key.
    numeric = {"keys": [{"kty": "RSA", "kid": "key-1", "n": 12345, "e": 65537}]}

    with pytest.raises(OIDCValidationError, match="signature verification failed"):
        _verify(_sign(_claims()), jwks=numeric)


def test_public_key_extraction_refuses_anything_that_is_not_an_rsa_jwk():
    with pytest.raises(OIDCValidationError, match="RSA required"):
        _public_key_from_jwk({"kty": "EC", "crv": "P-256"})
    with pytest.raises(OIDCValidationError, match="RSA required"):
        _public_key_from_jwk("not a jwk")


def test_a_header_that_is_not_a_json_object_is_rejected():
    not_json = "{0}.payload.sig".format(_b64url(b"definitely not json"))
    a_list = "{0}.payload.sig".format(_b64url(b"[1, 2]"))

    with pytest.raises(OIDCValidationError, match="invalid JWT header"):
        decode_unverified_header(not_json)
    with pytest.raises(OIDCValidationError, match="header is not an object"):
        decode_unverified_header(a_list)


# ── OIDC: JWKS selection ────────────────────────────────────────────────────


def test_a_token_without_a_kid_is_verified_against_every_rsa_key():
    token = _sign(_claims(), kid=None)

    claims = _verify(token, nonce="n-abc")

    assert claims["email"] == "alice@example.com"


def test_a_non_rsa_or_incomplete_jwk_is_refused():
    token = _sign(_claims(), kid=None)
    ec_only = {"keys": [{"kty": "EC", "crv": "P-256", "x": "a", "y": "b"}]}
    rsa_without_modulus = {"keys": [{"kty": "RSA", "kid": "key-1"}]}

    with pytest.raises(OIDCValidationError, match="signature verification failed"):
        _verify(token, jwks=ec_only)
    with pytest.raises(OIDCValidationError, match="signature verification failed"):
        _verify(token, jwks=rsa_without_modulus)


def test_claims_must_be_a_json_object_even_after_a_good_signature():
    not_json = _sign(None, payload_bytes=b"not json at all")
    a_list = _sign(None, payload_bytes=b"[1, 2, 3]")

    with pytest.raises(OIDCValidationError, match="invalid JWT claims JSON"):
        _verify(not_json)
    with pytest.raises(OIDCValidationError, match="claims are not an object"):
        _verify(a_list)


# ── OIDC: configuration + registered claims ─────────────────────────────────


def test_missing_token_or_configuration_fails_closed():
    with pytest.raises(OIDCValidationError, match="missing id_token"):
        _verify("")
    with pytest.raises(OIDCValidationError, match="issuer not configured"):
        _verify(_sign(_claims()), issuer="")
    with pytest.raises(OIDCValidationError, match="audience .* not configured"):
        _verify(_sign(_claims()), audience="")


def test_multi_audience_token_requires_a_matching_azp():
    token = _sign(_claims(aud=[AUDIENCE, "other-client"], azp="other-client"))

    with pytest.raises(OIDCValidationError, match="azp"):
        _verify(token, nonce="n-abc")


def test_future_iat_and_nbf_are_rejected():
    future_iat = _sign(_claims(iat=NOW + 3600))
    future_nbf = _sign(_claims(nbf=NOW + 3600))

    with pytest.raises(OIDCValidationError, match="'iat' is in the future"):
        _verify(future_iat, nonce="n-abc")
    with pytest.raises(OIDCValidationError, match="not yet valid"):
        _verify(future_nbf, nonce="n-abc")


# ── OIDC: JWKS fetch ────────────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, payload, *, error=None):
        self._payload = payload
        self._error = error

    def raise_for_status(self):
        if self._error is not None:
            raise self._error

    def json(self):
        return self._payload


class _FakeAsyncClient:
    calls: list = []
    response = _FakeResponse({"keys": []})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get(self, url, timeout=None):
        type(self).calls.append((url, timeout))
        return type(self).response


def test_fetch_jwks_requires_a_discovery_uri():
    import asyncio

    with pytest.raises(OIDCValidationError, match="no 'jwks_uri'"):
        asyncio.run(fetch_jwks(""))


def test_fetch_jwks_returns_the_provider_document(monkeypatch):
    import asyncio

    import httpx

    _FakeAsyncClient.calls = []
    _FakeAsyncClient.response = _FakeResponse({"keys": [{"kty": "RSA", "kid": "k1"}]})
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setitem(sys.modules, "httpx", httpx)

    document = asyncio.run(fetch_jwks("https://idp.example.com/jwks", timeout=3.0))

    assert document == {"keys": [{"kty": "RSA", "kid": "k1"}]}
    assert _FakeAsyncClient.calls == [("https://idp.example.com/jwks", 3.0)]


def test_fetch_jwks_propagates_a_provider_http_error(monkeypatch):
    import asyncio

    import httpx

    _FakeAsyncClient.calls = []
    _FakeAsyncClient.response = _FakeResponse(None, error=RuntimeError("502 from idp"))
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    with pytest.raises(RuntimeError, match="502 from idp"):
        asyncio.run(fetch_jwks("https://idp.example.com/jwks"))
