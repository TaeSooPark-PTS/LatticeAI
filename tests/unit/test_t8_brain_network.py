"""T8: device identity, signed bundles, Brain Network peer exchange.

Sovereignty contract: exports are signed by the device key; tampered
bundles are refused; pre-v4 unsigned bundles import locally with
origin='unsigned-legacy'; the peer path requires pairing, fresh
timestamps, unseen nonces, and a bundle signed by that exact peer.
"""

import json
import time

import pytest

from knowledge_graph import KnowledgeGraphStore
from lattice_brain.graph.identity import DeviceIdentity
from lattice_brain.graph.network import (
    HEADER_TIMESTAMP,
    BrainNetwork,
)
from lattice_brain.portability import KGPortabilityService


def _stack(tmp_path, name):
    d = tmp_path / name
    d.mkdir()
    kg = KnowledgeGraphStore(d / "kg.sqlite", d / "blobs")
    identity = DeviceIdentity(d, use_keyring=False)
    portability = KGPortabilityService(
        knowledge_graph=kg, data_dir=d, enable_graph=True, device_identity=identity,
    )
    network = BrainNetwork(identity=identity, portability=portability, data_dir=d)
    return kg, identity, portability, network


def test_export_is_signed_and_tamper_refused(tmp_path):
    kg, identity, portability, _ = _stack(tmp_path, "a")
    kg.ingest_message("user", "signed content", user_email="a@b.c")
    artifact = portability.export()
    assert artifact["signature"]["fingerprint"] == identity.fingerprint

    result = portability.import_data(artifact, dry_run=True)
    assert result["signed"] is True
    assert result["origin"].startswith("device:")

    artifact["header"]["exported_at"] = "tampered"
    with pytest.raises(ValueError, match="signature"):
        portability.import_data(artifact, dry_run=True)


def test_unsigned_legacy_bundle_imports_locally(tmp_path):
    kg, _, portability, _ = _stack(tmp_path, "a")
    kg.ingest_message("user", "legacy data", user_email="a@b.c")
    artifact = portability.export()
    artifact.pop("signature")  # a v3.6-format unsigned export
    result = portability.import_data(artifact, dry_run=True)
    assert result["origin"] == "unsigned-legacy"
    assert result["signed"] is False


def test_pairing_validates_keys_and_dedupes(tmp_path):
    _, identity_b, _, _ = _stack(tmp_path, "b")
    _, _, _, network_a = _stack(tmp_path, "a")
    peer = network_a.add_peer(name="laptop", base_url="http://10.0.0.2:4825", public_key=identity_b.public_key_b64)
    assert peer["fingerprint"] == identity_b.fingerprint
    with pytest.raises(ValueError, match="already paired"):
        network_a.add_peer(name="dup", base_url="http://x", public_key=identity_b.public_key_b64)
    with pytest.raises(ValueError, match="Ed25519"):
        network_a.add_peer(name="bad", base_url="http://x", public_key="not-a-key")


def test_peer_request_auth_rejects_unpaired_stale_and_replay(tmp_path):
    _, _, _, network_a = _stack(tmp_path, "a")          # receiver
    _, identity_b, _, network_b = _stack(tmp_path, "b")  # sender

    body = b'{"probe": true}'
    headers = network_b.auth_headers(body)

    # Unpaired sender is refused.
    with pytest.raises(PermissionError, match="not a paired peer"):
        network_a.verify_peer_request(headers, body)

    network_a.add_peer(name="b", base_url="http://b", public_key=identity_b.public_key_b64)
    peer = network_a.verify_peer_request(headers, body)
    assert peer["public_key"] == identity_b.public_key_b64

    # Replay of the same nonce is refused.
    with pytest.raises(PermissionError, match="replayed"):
        network_a.verify_peer_request(headers, body)

    # Stale timestamp is refused.
    stale = network_b.auth_headers(body)
    stale[HEADER_TIMESTAMP] = str(int(time.time()) - 3600)
    with pytest.raises(PermissionError, match="freshness"):
        network_a.verify_peer_request(stale, body)

    # Tampered body fails the signature.
    fresh = network_b.auth_headers(body)
    with pytest.raises(PermissionError, match="signature"):
        network_a.verify_peer_request(fresh, b'{"probe": false}')


def test_end_to_end_receive_imports_with_origin(tmp_path):
    kg_a, _, _, network_a = _stack(tmp_path, "a")            # receiver
    kg_b, identity_b, portability_b, network_b = _stack(tmp_path, "b")  # sender

    network_a.add_peer(name="b", base_url="http://b", public_key=identity_b.public_key_b64)
    kg_b.ingest_message("user", "knowledge exchanged across brains", user_email="b@x.com")

    artifact = portability_b.export()
    body = json.dumps(artifact, ensure_ascii=False).encode("utf-8")
    headers = network_b.auth_headers(body)

    result = network_a.receive(headers, body)
    assert result["origin"] == f"device:{identity_b.fingerprint}"
    assert result["peer"]["name"] == "b"
    matches = kg_a.search("exchanged")["matches"]
    assert matches, "imported knowledge must be queryable in the receiving brain"


def test_receive_refuses_bundle_signed_by_other_device(tmp_path):
    _, _, _, network_a = _stack(tmp_path, "a")
    kg_b, identity_b, _, network_b = _stack(tmp_path, "b")
    _, _, portability_c, _ = _stack(tmp_path, "c")  # a third device's bundle

    network_a.add_peer(name="b", base_url="http://b", public_key=identity_b.public_key_b64)
    artifact = portability_c.export()  # signed by C, transported by B
    body = json.dumps(artifact, ensure_ascii=False).encode("utf-8")
    headers = network_b.auth_headers(body)
    with pytest.raises(PermissionError, match="signer does not match"):
        network_a.receive(headers, body)


def test_workspace_export_really_filters(tmp_path):
    """The workspace_id parameter filters the DATA, not just the header."""
    kg, _, portability, _ = _stack(tmp_path, "a")
    with kg._connect() as conn:
        kg._upsert_node(conn, "n-org", "Concept", "org secret", "", {},
                        workspace_id="org-acme", visibility="workspace")
        kg._upsert_node(conn, "n-other", "Concept", "other workspace", "", {},
                        workspace_id="org-zeta", visibility="workspace")
        kg._upsert_node(conn, "n-legacy", "Concept", "legacy global", "", {})

    artifact = portability.export(workspace_id="org-acme")
    ids = {n["id"] for n in artifact["nodes"]}
    assert "n-org" in ids and "n-legacy" in ids
    assert "n-other" not in ids, "another workspace's rows must not leak into the bundle"
