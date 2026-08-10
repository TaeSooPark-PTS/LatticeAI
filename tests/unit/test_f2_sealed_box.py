"""v11.2.0 F2 — sealing a shared subgraph to a recipient's public key.

The 11.1.0 limitation this closes was stated plainly in FEATURE_STATUS: the
bundle was encrypted with a *shared passphrase*, so the secret had to reach the
receiver through some other channel first. An X25519 sealed box removes that
step, and the promises worth testing are the ones that are easy to claim:

* only the intended recipient can open a bundle — a different key fails, and
  fails with a reason;
* the sender's Ed25519 signature is **untouched**: signing says who wrote it,
  sealing says who may read it, and the receiving Brain still verifies both;
* the two mechanisms stay separable — one bundle is never both, and asking for
  neither is refused rather than silently written in the clear.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cryptography.hazmat.primitives.asymmetric.x25519 import (  # noqa: E402
    X25519PrivateKey,
)

from lattice_brain.graph.identity import DeviceIdentity  # noqa: E402
from lattice_brain.graph.store import KnowledgeGraphStore  # noqa: E402
from lattice_brain.ingestion import IngestionItem, IngestionPipeline  # noqa: E402
from lattice_brain.portability import (  # noqa: E402
    BRAIN_NETWORK_ENV,
    ENCRYPTION_MODES,
    KGPortabilityService,
)
from lattice_brain.sealed_box import (  # noqa: E402
    SEALED_BOX_ALGORITHM,
    RecipientIdentity,
    load_recipient_identity,
    public_key_fingerprint,
    seal,
    unseal,
)
from latticeai.api.portability import create_portability_router  # noqa: E402
from latticeai.services.review_queue import ReviewQueueService  # noqa: E402

ADMIN = {"X-Test-Admin": "true"}


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv(BRAIN_NETWORK_ENV, "1")
    return True


class _ReviewStore:
    def __init__(self) -> None:
        self.items: Dict[str, Dict[str, Any]] = {}

    def create_review_item(self, **fields: Any) -> Dict[str, Any]:
        item_id = f"review-{len(self.items) + 1}"
        item = {"id": item_id, "status": "pending", "snoozed_until": None, **fields}
        self.items[item_id] = item
        return item

    def get_review_item(self, item_id: str, *, workspace_id: Optional[str] = None):
        if item_id not in self.items:
            raise FileNotFoundError(item_id)
        return self.items[item_id]

    def update_review_item(self, item_id: str, *, workspace_id=None, **fields: Any):
        self.items[item_id].update(fields)
        return self.items[item_id]

    def list_review_items(self, **_: Any) -> List[Dict[str, Any]]:
        return list(self.items.values())


def _brain(tmp_path: Path, tag: str, *, seed: bool = False):
    store = KnowledgeGraphStore(tmp_path / f"{tag}.sqlite", tmp_path / f"{tag}-blobs")
    data_dir = tmp_path / f"{tag}-data"
    data_dir.mkdir(parents=True, exist_ok=True)
    service = KGPortabilityService(
        knowledge_graph=store,
        data_dir=data_dir,
        device_identity=DeviceIdentity(data_dir, use_keyring=False),
    )
    node_ids: List[str] = []
    if seed:
        pipeline = IngestionPipeline(store)
        for title, body in (
            ("Retrieval plan", "하이브리드 검색은 렉시컬과 벡터를 함께 씁니다."),
            ("Release ritual", "릴리스는 태그와 노트를 함께 남깁니다."),
        ):
            result = pipeline.ingest(
                IngestionItem(
                    source_type="note", title=title, text=body, workspace_id="ws-1",
                ),
                user_email="me@local",
            )
            node_ids.append(result.node_id)
    return service, store, node_ids


# ── the primitive ────────────────────────────────────────────────────────────
def test_only_the_recipients_private_key_opens_a_sealed_box():
    recipient = X25519PrivateKey.generate()
    stranger = X25519PrivateKey.generate()
    public = public_bytes_b64(recipient)

    block = seal(b"a shared decision", recipient_public_key=public)
    assert block["algorithm"] == SEALED_BOX_ALGORITHM
    assert unseal(block, recipient) == b"a shared decision"

    with pytest.raises(ValueError, match="sealed to a different recipient key"):
        unseal(block, stranger)


def test_two_seals_of_the_same_bytes_never_look_alike():
    """Fresh ephemeral key + salt + nonce per box, so the ciphertext is unique."""
    recipient = X25519PrivateKey.generate()
    public = public_bytes_b64(recipient)
    first = seal(b"same words", recipient_public_key=public)
    second = seal(b"same words", recipient_public_key=public)

    assert first["ciphertext"] != second["ciphertext"]
    assert first["ephemeral_public_key"] != second["ephemeral_public_key"]
    assert unseal(first, recipient) == unseal(second, recipient) == b"same words"


def test_a_tampered_box_fails_the_tag_check_rather_than_decrypting_anyway():
    recipient = X25519PrivateKey.generate()
    block = seal(b"do not edit me", recipient_public_key=public_bytes_b64(recipient))
    block["ciphertext"] = block["ciphertext"][:-4] + "AAAA"
    with pytest.raises(ValueError, match="decryption failed"):
        unseal(block, recipient)


def test_every_malformed_envelope_says_which_check_failed():
    recipient = X25519PrivateKey.generate()
    good = seal(b"x", recipient_public_key=public_bytes_b64(recipient))

    with pytest.raises(ValueError, match="unsupported sealed-box algorithm: missing"):
        unseal({}, recipient)
    with pytest.raises(ValueError, match="unsupported sealed-box algorithm: rot13"):
        unseal({"algorithm": "rot13"}, recipient)
    missing = {key: value for key, value in good.items() if key != "salt"}
    with pytest.raises(ValueError, match="missing encryption metadata"):
        unseal(missing, recipient)


def test_an_unaddressed_box_is_still_openable_by_whoever_holds_the_key():
    """``recipient_public_key`` is a courtesy label, not the security boundary."""
    recipient = X25519PrivateKey.generate()
    block = seal(b"anonymous", recipient_public_key=public_bytes_b64(recipient))
    block.pop("recipient_public_key")
    assert unseal(block, recipient) == b"anonymous"


def test_garbage_is_refused_as_a_public_key_before_anything_is_encrypted():
    with pytest.raises(ValueError, match="not a valid X25519 public key"):
        seal(b"x", recipient_public_key="not-a-key")
    with pytest.raises(ValueError, match="not a valid X25519 public key"):
        public_key_fingerprint("////")


# ── the stored identity ──────────────────────────────────────────────────────
def test_the_receiving_key_is_created_once_and_then_reloaded(tmp_path):
    first = RecipientIdentity(tmp_path / "data")
    key_file = tmp_path / "data" / "subgraph_recipient.key"
    assert key_file.exists()
    assert oct(key_file.stat().st_mode)[-3:] == "600"

    second = RecipientIdentity(tmp_path / "data")
    assert second.public_key_b64 == first.public_key_b64
    assert second.fingerprint == first.fingerprint

    described = first.describe()
    assert described["algorithm"] == SEALED_BOX_ALGORITHM
    assert described["storage"] == "file"
    assert described["path"] == str(key_file)
    # A round trip to itself is the honest way to prove the pair matches.
    assert first.unseal(first.seal_to_self(b"hello")) == b"hello"


def test_an_unusable_data_directory_is_a_state_not_a_crash(tmp_path):
    blocked = tmp_path / "afile"
    blocked.write_text("not a directory", encoding="utf-8")
    assert load_recipient_identity(blocked / "nested") is None
    assert load_recipient_identity(tmp_path / "fresh") is not None


# ── the service ──────────────────────────────────────────────────────────────
def test_share_status_now_names_both_mechanisms(tmp_path, enabled):
    service, _, _ = _brain(tmp_path, "src", seed=True)
    status = service.share_status()
    assert status["encryption"] == list(ENCRYPTION_MODES)
    assert status["recipient_public_key_encryption"] is True
    assert status["sealed_box_algorithm"] == SEALED_BOX_ALGORITHM
    assert status["gate"]["flag"] == BRAIN_NETWORK_ENV


def test_a_bundle_sealed_to_a_peer_round_trips_and_keeps_its_signature(tmp_path, enabled):
    sender, _, node_ids = _brain(tmp_path, "src", seed=True)
    receiver, _, _ = _brain(tmp_path, "dst")
    key = receiver.recipient_key()
    assert key["available"] is True

    written = sender.export_subgraph_archive(
        tmp_path / "sealed",
        recipient_public_key=key["public_key"],
        node_ids=[node_ids[0]],
        workspace_id="ws-1",
    )
    assert written["encryption"] == "recipient_public_key"
    assert written["recipient_fingerprint"] == key["fingerprint"]

    envelope = json.loads(Path(written["path"]).read_text(encoding="utf-8"))
    # The header and signature stay outside the ciphertext: a recipient can see
    # who sent it before deciding to open it.
    assert envelope["header"]["device"]["fingerprint"]
    assert envelope["signature"]["algorithm"] == "ed25519"
    assert "payload" not in envelope and envelope["sealed_box"]["ciphertext"]

    artifact = receiver.read_subgraph_archive(written["path"])
    verdict = receiver.verify_subgraph(artifact)
    assert verdict["ok"] is True and verdict["fingerprint"]


def test_a_bundle_sealed_to_someone_else_will_not_open_here(tmp_path, enabled):
    sender, _, node_ids = _brain(tmp_path, "src", seed=True)
    receiver, _, _ = _brain(tmp_path, "dst")
    stranger = RecipientIdentity(tmp_path / "stranger")

    written = sender.export_subgraph_archive(
        tmp_path / "for-stranger",
        recipient_public_key=stranger.public_key_b64,
        node_ids=[node_ids[0]],
        workspace_id="ws-1",
    )
    with pytest.raises(ValueError, match="sealed to a different recipient key"):
        receiver.read_subgraph_archive(written["path"])


def test_choosing_both_or_neither_mechanism_is_refused(tmp_path, enabled):
    sender, _, node_ids = _brain(tmp_path, "src", seed=True)
    recipient = RecipientIdentity(tmp_path / "peer")

    with pytest.raises(ValueError, match="Choose one"):
        sender.export_subgraph_archive(
            tmp_path / "both",
            passphrase="hunter2",
            recipient_public_key=recipient.public_key_b64,
            node_ids=[node_ids[0]],
        )
    with pytest.raises(ValueError, match="must be encrypted"):
        sender.export_subgraph_archive(tmp_path / "neither", node_ids=[node_ids[0]])
    with pytest.raises(ValueError, match="not a valid X25519 public key"):
        sender.export_subgraph_archive(
            tmp_path / "junk", recipient_public_key="nope", node_ids=[node_ids[0]],
        )


def test_the_passphrase_bundle_is_unchanged_and_still_needs_its_passphrase(tmp_path, enabled):
    sender, _, node_ids = _brain(tmp_path, "src", seed=True)
    written = sender.export_subgraph_archive(
        tmp_path / "classic", passphrase="hunter2", node_ids=[node_ids[0]],
    )
    assert written["encryption"] == "passphrase"
    assert written["recipient_fingerprint"] is None

    back = sender.read_subgraph_archive(written["path"], passphrase="hunter2")
    assert sender.verify_subgraph(back)["ok"] is True
    with pytest.raises(ValueError, match="a passphrase is required"):
        sender.read_subgraph_archive(written["path"])


def test_a_sealed_bundle_on_a_brain_with_no_receiving_key_says_so(tmp_path, enabled, monkeypatch):
    sender, _, node_ids = _brain(tmp_path, "src", seed=True)
    receiver, _, _ = _brain(tmp_path, "dst")
    peer = RecipientIdentity(tmp_path / "peer")
    written = sender.export_subgraph_archive(
        tmp_path / "sealed", recipient_public_key=peer.public_key_b64,
        node_ids=[node_ids[0]],
    )
    monkeypatch.setattr(
        "lattice_brain.portability.load_recipient_identity", lambda _dir: None
    )
    assert receiver.recipient_key() == {
        "available": False,
        "detail": (
            "a receiving key could not be created in the data directory; "
            "sealed bundles cannot be opened on this machine"
        ),
    }
    with pytest.raises(ValueError, match="no receiving key"):
        receiver.read_subgraph_archive(written["path"])


def test_a_sealed_envelope_missing_its_box_is_refused(tmp_path, enabled):
    receiver, _, _ = _brain(tmp_path, "dst")
    broken = tmp_path / "broken.latticebrain"
    broken.write_text(
        json.dumps({
            "format": "latticebrain.subgraph",
            "encryption": "recipient_public_key",
            "header": {},
            "signature": {},
        }),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="missing encryption metadata"):
        receiver.read_subgraph_archive(broken)


def test_the_share_gate_still_guards_the_recipient_key(tmp_path, monkeypatch):
    monkeypatch.delenv(BRAIN_NETWORK_ENV, raising=False)
    service, _, _ = _brain(tmp_path, "src")
    with pytest.raises(PermissionError):
        service.recipient_key()


# ── the routes ───────────────────────────────────────────────────────────────
def _client(service):
    app = FastAPI()
    review = ReviewQueueService(store=_ReviewStore())

    def _require_user(request: Request) -> str:
        return "admin@local"

    def _require_admin(request: Request) -> str:
        if request.headers.get("X-Test-Admin") != "true":
            raise PermissionError("admin required")
        return "admin@local"

    app.include_router(create_portability_router(
        service=service,
        require_user=_require_user,
        require_admin=_require_admin,
        review_queue=review,
    ))
    return TestClient(app)


def test_the_routes_publish_a_key_and_accept_a_sealed_bundle(tmp_path, enabled):
    sender, _, node_ids = _brain(tmp_path, "src", seed=True)
    receiver, _, _ = _brain(tmp_path, "dst")
    sending = _client(sender)
    receiving = _client(receiver)

    key = receiving.get(
        "/api/knowledge-graph/share/recipient-key", headers=ADMIN,
    ).json()
    assert key["available"] is True and key["public_key"]

    written = sending.post(
        "/api/knowledge-graph/share/archive",
        json={
            "node_ids": [node_ids[0]],
            "workspace_id": "ws-1",
            "path": str(tmp_path / "wire.latticebrain"),
            "recipient_public_key": key["public_key"],
        },
        headers=ADMIN,
    )
    assert written.status_code == 200
    assert written.json()["encryption"] == "recipient_public_key"

    # No passphrase in the request at all — the envelope says how it was sealed.
    received = receiving.post(
        "/api/knowledge-graph/share/import",
        json={"path": written.json()["path"], "dry_run": True},
        headers=ADMIN,
    )
    assert received.status_code == 200
    assert received.json()["status"] == "dry_run"


def test_the_import_route_still_insists_on_a_path_or_an_artifact(tmp_path, enabled):
    receiver, _, _ = _brain(tmp_path, "dst")
    client = _client(receiver)
    response = client.post(
        "/api/knowledge-graph/share/import", json={"dry_run": True}, headers=ADMIN,
    )
    assert response.status_code == 400
    assert "path" in response.json()["detail"]


def test_the_recipient_key_route_reports_the_gate_when_sharing_is_off(tmp_path, monkeypatch):
    monkeypatch.delenv(BRAIN_NETWORK_ENV, raising=False)
    service, _, _ = _brain(tmp_path, "src")
    response = _client(service).get(
        "/api/knowledge-graph/share/recipient-key", headers=ADMIN,
    )
    assert response.status_code == 403


def public_bytes_b64(private_key: X25519PrivateKey) -> str:
    from lattice_brain.sealed_box import _b64, _raw_public

    return _b64(_raw_public(private_key.public_key()))
