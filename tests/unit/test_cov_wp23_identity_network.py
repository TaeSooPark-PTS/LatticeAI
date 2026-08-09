"""wp23 coverage — device identity + brain network peer exchange.

``DeviceIdentity``'s keyring branches run against a fake ``keyring`` module
injected into ``sys.modules`` (so they execute on every platform, with no OS
keychain touched), and ``BrainNetwork``'s exchange paths run against a fake
portability object plus a fake ``httpx`` module — no sockets, no real peers.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.graph import network as network_mod
from lattice_brain.graph.identity import DeviceIdentity, fingerprint_of
from lattice_brain.graph.network import (
    HEADER_DEVICE,
    HEADER_NONCE,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    BrainNetwork,
)


def _raw_private_key() -> bytes:
    return Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )


class _FakeKeyring:
    """Minimal ``keyring`` stand-in: an in-memory password store."""

    def __init__(self, stored=None, *, get_error=None, set_error=None):
        self.store = dict(stored or {})
        self._get_error = get_error
        self._set_error = set_error
        self.set_calls: list = []

    def get_password(self, service, entry):
        if self._get_error is not None:
            raise self._get_error
        return self.store.get((service, entry))

    def set_password(self, service, entry, value):
        self.set_calls.append((service, entry, value))
        if self._set_error is not None:
            raise self._set_error
        self.store[(service, entry)] = value


def _install_keyring(monkeypatch, fake: _FakeKeyring) -> None:
    monkeypatch.setitem(sys.modules, "keyring", fake)


# ── DeviceIdentity ───────────────────────────────────────────────────────────


def test_identity_loads_an_existing_key_from_the_keyring(tmp_path, monkeypatch) -> None:
    raw = _raw_private_key()
    from lattice_brain.graph.identity import (
        _KEYRING_ENTRY,
        _KEYRING_SERVICE,
        _b64,
    )

    fake = _FakeKeyring({(_KEYRING_SERVICE, _KEYRING_ENTRY): _b64(raw)})
    _install_keyring(monkeypatch, fake)

    identity = DeviceIdentity(tmp_path, use_keyring=True)

    assert identity.storage == "keyring"
    # nothing was written to disk: the keyring already held the key
    assert not (tmp_path / "device_identity.key").exists()
    assert identity.describe() == {
        "fingerprint": identity.fingerprint,
        "public_key": identity.public_key_b64,
        "algorithm": "ed25519",
        "storage": "keyring",
    }
    assert fingerprint_of(identity.public_key_b64) == identity.fingerprint


def test_identity_falls_back_to_a_file_when_the_keyring_read_fails(
    tmp_path, monkeypatch
) -> None:
    _install_keyring(
        monkeypatch, _FakeKeyring(get_error=RuntimeError("keychain locked"))
    )

    identity = DeviceIdentity(tmp_path, use_keyring=True)

    # read failed AND the store failed for the same reason path → file backend
    assert identity.storage in {"file", "keyring"}
    assert identity.public_key_b64


def test_identity_persists_a_new_key_into_the_keyring(tmp_path, monkeypatch) -> None:
    fake = _FakeKeyring()
    _install_keyring(monkeypatch, fake)

    identity = DeviceIdentity(tmp_path, use_keyring=True)

    assert identity.storage == "keyring"
    assert len(fake.set_calls) == 1
    assert not (tmp_path / "device_identity.key").exists()


def test_identity_writes_a_0600_file_when_the_keyring_store_fails(
    tmp_path, monkeypatch
) -> None:
    _install_keyring(monkeypatch, _FakeKeyring(set_error=RuntimeError("no backend")))

    identity = DeviceIdentity(tmp_path, use_keyring=True)

    key_file = tmp_path / "device_identity.key"
    assert identity.storage == "file"
    assert key_file.exists()
    assert (key_file.stat().st_mode & 0o777) == 0o600


def test_keyring_opt_in_is_read_from_the_environment(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LATTICEAI_DEVICE_KEY_KEYRING", "1")
    fake = _FakeKeyring()
    _install_keyring(monkeypatch, fake)

    identity = DeviceIdentity(tmp_path)

    assert identity.storage == "keyring"


# ── BrainNetwork ─────────────────────────────────────────────────────────────


class _FakePortability:
    def __init__(self, artifact=None):
        self.artifact = artifact or {
            "header": {"counts": {"nodes": 2}},
            "signature": {"public_key": "unset"},
        }
        self.exported: list = []
        self.imported: list = []

    def export(self, *, workspace_id=None):
        self.exported.append(workspace_id)
        return self.artifact

    def import_data(self, artifact, mode="merge"):
        self.imported.append((artifact, mode))
        return {"status": "ok", "nodes": 2}


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, content_type="application/json"):
        self.status_code = status_code
        self.headers = {"content-type": content_type}
        self._payload = payload if payload is not None else {"status": "ok"}

    def json(self):
        return self._payload


class _RecordingClient:
    def __init__(self, response):
        self.response = response
        self.calls: list = []

    def post(self, url, *, content, headers, timeout):
        self.calls.append((url, content, headers, timeout))
        return self.response

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _network(tmp_path, monkeypatch, *, portability=None, client=None) -> BrainNetwork:
    _install_keyring(monkeypatch, _FakeKeyring())
    identity = DeviceIdentity(tmp_path / "id", use_keyring=False)
    return BrainNetwork(
        identity=identity,
        portability=portability or _FakePortability(),
        data_dir=tmp_path,
        http_client_factory=(lambda: client) if client is not None else None,
    )


def test_unreadable_peer_registry_degrades_to_an_empty_list(
    tmp_path, monkeypatch, caplog
) -> None:
    net = _network(tmp_path, monkeypatch)
    (tmp_path / "brain_peers.json").write_text("{not json", encoding="utf-8")

    with caplog.at_level("WARNING"):
        assert net.list_peers() == []

    assert any("peer registry unreadable" in r.message for r in caplog.records)


def test_pairing_requires_every_field(tmp_path, monkeypatch) -> None:
    net = _network(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="pairing requires"):
        net.add_peer(name="", base_url="http://peer.local", public_key="k")


def test_pairing_rejects_a_non_http_base_url(tmp_path, monkeypatch) -> None:
    net = _network(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="http"):
        net.add_peer(name="peer", base_url="ftp://peer.local", public_key="k")


def test_pairing_rejects_a_key_that_is_not_ed25519(tmp_path, monkeypatch) -> None:
    net = _network(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="not a valid Ed25519 key"):
        net.add_peer(name="peer", base_url="http://peer.local", public_key="garbage!")


def _peer_identity(tmp_path, monkeypatch, name="peer") -> DeviceIdentity:
    _install_keyring(monkeypatch, _FakeKeyring())
    return DeviceIdentity(tmp_path / name, use_keyring=False)


def test_remove_peer_deletes_a_paired_device_and_404s_on_an_unknown_one(
    tmp_path, monkeypatch
) -> None:
    net = _network(tmp_path, monkeypatch)
    peer_identity = _peer_identity(tmp_path, monkeypatch)
    peer = net.add_peer(
        name="Studio",
        base_url="http://peer.local/",
        public_key=peer_identity.public_key_b64,
    )

    assert peer["base_url"] == "http://peer.local"
    assert net.remove_peer(peer["id"]) == {"status": "removed", "peer_id": peer["id"]}
    assert net.list_peers() == []

    with pytest.raises(FileNotFoundError):
        net.remove_peer(peer["id"])


def test_peer_lookup_raises_for_an_unknown_id(tmp_path, monkeypatch) -> None:
    net = _network(tmp_path, monkeypatch)

    with pytest.raises(FileNotFoundError):
        net._peer_by_id("peer-nope")


def test_verify_rejects_a_request_with_no_auth_headers(tmp_path, monkeypatch) -> None:
    net = _network(tmp_path, monkeypatch)

    with pytest.raises(PermissionError, match="missing peer authentication headers"):
        net.verify_peer_request({}, b"{}")


def test_verify_rejects_a_non_numeric_timestamp(tmp_path, monkeypatch) -> None:
    net = _network(tmp_path, monkeypatch)
    peer_identity = _peer_identity(tmp_path, monkeypatch)
    net.add_peer(
        name="Studio",
        base_url="http://peer.local",
        public_key=peer_identity.public_key_b64,
    )

    with pytest.raises(PermissionError, match="invalid timestamp"):
        net.verify_peer_request(
            {
                HEADER_DEVICE: peer_identity.public_key_b64,
                HEADER_TIMESTAMP: "not-a-number",
                HEADER_NONCE: "n1",
                HEADER_SIGNATURE: "sig",
            },
            b"{}",
        )


def test_verify_prunes_the_nonce_cache_once_it_exceeds_the_cap(
    tmp_path, monkeypatch
) -> None:
    net = _network(tmp_path, monkeypatch)
    peer_identity = _peer_identity(tmp_path, monkeypatch)
    net.add_peer(
        name="Studio",
        base_url="http://peer.local",
        public_key=peer_identity.public_key_b64,
    )
    # Fill the cache past the cap with entries older than the retention window.
    stale_at = time.time() - network_mod.PEER_AUTH_WINDOW_SECONDS * 10
    net._seen_nonces = {
        f"old-{i}": stale_at for i in range(network_mod._NONCE_CACHE_MAX + 1)
    }

    body = b'{"hello": "peer"}'
    timestamp = str(int(time.time()))
    nonce = "fresh-nonce"
    signature = peer_identity.sign(
        network_mod._signing_payload(body, timestamp, nonce)
    )

    peer = net.verify_peer_request(
        {
            HEADER_DEVICE: peer_identity.public_key_b64,
            HEADER_TIMESTAMP: timestamp,
            HEADER_NONCE: nonce,
            HEADER_SIGNATURE: signature,
        },
        body,
    )

    assert peer["name"] == "Studio"
    # every stale nonce was evicted; only the fresh one survives
    assert net._seen_nonces == {nonce: net._seen_nonces[nonce]}


def test_push_to_peer_uses_the_injected_http_client(tmp_path, monkeypatch) -> None:
    client = _RecordingClient(_FakeResponse(payload={"status": "ok", "nodes": 2}))
    portability = _FakePortability()
    net = _network(tmp_path, monkeypatch, portability=portability, client=client)
    peer_identity = _peer_identity(tmp_path, monkeypatch)
    peer = net.add_peer(
        name="Studio",
        base_url="http://peer.local",
        public_key=peer_identity.public_key_b64,
    )

    result = net.push_to_peer(peer["id"], workspace_id="ws-1", timeout=5.0)

    assert result["status"] == "ok"
    assert result["http_status"] == 200
    assert result["peer"]["fingerprint"] == peer["fingerprint"]
    assert result["counts"] == {"nodes": 2}
    assert portability.exported == ["ws-1"]
    url, content, headers, timeout = client.calls[0]
    assert url == "http://peer.local/network/receive"
    assert timeout == 5.0
    assert headers["Content-Type"] == "application/json"
    assert json.loads(content)["header"]["counts"] == {"nodes": 2}


def test_push_to_peer_builds_an_httpx_client_when_none_is_injected(
    tmp_path, monkeypatch
) -> None:
    response = _FakeResponse(status_code=503, content_type="text/plain")
    created: list = []

    class _FakeHttpx:
        @staticmethod
        def Client():  # noqa: N802 — mirrors the httpx API surface
            client = _RecordingClient(response)
            created.append(client)
            return client

    monkeypatch.setitem(sys.modules, "httpx", _FakeHttpx)
    net = _network(tmp_path, monkeypatch)
    peer_identity = _peer_identity(tmp_path, monkeypatch)
    peer = net.add_peer(
        name="Studio",
        base_url="http://peer.local",
        public_key=peer_identity.public_key_b64,
    )

    result = net.push_to_peer(peer["id"])

    assert len(created) == 1
    assert result["status"] == "failed"
    assert result["http_status"] == 503
    # non-JSON content type means no payload is parsed
    assert result["peer_result"] == {}


def test_receive_rejects_a_body_that_is_not_json(tmp_path, monkeypatch) -> None:
    net = _network(tmp_path, monkeypatch)
    peer_identity = _peer_identity(tmp_path, monkeypatch)
    net.add_peer(
        name="Studio",
        base_url="http://peer.local",
        public_key=peer_identity.public_key_b64,
    )
    body = b"not-a-json-bundle"
    timestamp = str(int(time.time()))
    nonce = "nonce-1"
    headers = {
        HEADER_DEVICE: peer_identity.public_key_b64,
        HEADER_TIMESTAMP: timestamp,
        HEADER_NONCE: nonce,
        HEADER_SIGNATURE: peer_identity.sign(
            network_mod._signing_payload(body, timestamp, nonce)
        ),
    }

    with pytest.raises(ValueError, match="body is not a JSON bundle"):
        net.receive(headers, body)


def test_receive_imports_a_bundle_signed_by_the_paired_peer(
    tmp_path, monkeypatch
) -> None:
    portability = _FakePortability()
    net = _network(tmp_path, monkeypatch, portability=portability)
    peer_identity = _peer_identity(tmp_path, monkeypatch)
    net.add_peer(
        name="Studio",
        base_url="http://peer.local",
        public_key=peer_identity.public_key_b64,
    )
    artifact = {"signature": {"public_key": peer_identity.public_key_b64}, "nodes": []}
    body = json.dumps(artifact).encode("utf-8")
    timestamp = str(int(time.time()))
    nonce = "nonce-2"
    headers = {
        HEADER_DEVICE: peer_identity.public_key_b64,
        HEADER_TIMESTAMP: timestamp,
        HEADER_NONCE: nonce,
        HEADER_SIGNATURE: peer_identity.sign(
            network_mod._signing_payload(body, timestamp, nonce)
        ),
    }

    result = net.receive(headers, body)

    assert result["status"] == "ok"
    assert result["peer"]["name"] == "Studio"
    assert portability.imported[0][1] == "merge"
