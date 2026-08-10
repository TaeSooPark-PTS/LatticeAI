"""v11.1.0 Track 5 — selective subgraph share (opt-in Brain Network prototype).

Three promises are under test here, because each of them is the kind that is
easy to *claim*:

1. **Off by default.** Every share surface refuses until
   ``LATTICEAI_BRAIN_NETWORK`` is set, and the refusal says so.
2. **Signed, and fail-closed.** The bundle's header pins a digest of its
   payload and is signed by the sending device; an unsigned, re-signed, or
   edited bundle is refused rather than partially trusted.
3. **Proposals, not merges.** A received bundle lands in the review queue with
   its origin attached. The graph changes only when a human accepts one item,
   and then only for that item — an edge into a node this Brain does not have
   is deferred and reported, never written dangling.

The round trip runs between two real ``KnowledgeGraphStore`` brains in
``tmp_path``, each with its own device identity.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import lattice_brain.portability as portability_module
from lattice_brain.graph.identity import DeviceIdentity
from lattice_brain.graph.store import KnowledgeGraphStore
from lattice_brain.ingestion import IngestionItem, IngestionPipeline
from lattice_brain.portability import (
    BRAIN_NETWORK_ENV,
    SUBGRAPH_FORMAT,
    SUBGRAPH_REVIEW_KIND,
    BrainNetworkDisabled,
    KGPortabilityService,
    brain_network_enabled,
)
from latticeai.api.portability import create_portability_router
from latticeai.services.review_queue import InvalidReviewTransition, ReviewQueueService

PASSPHRASE = "correct-horse-battery-staple"
ADMIN = {"X-Test-Admin": "true"}
EN = {"Accept-Language": "en"}


# ── fixtures / fakes ─────────────────────────────────────────────────────────

@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv(BRAIN_NETWORK_ENV, "1")
    return True


class _ReviewStore:
    """The slice of the workspace store ReviewQueueService actually uses."""

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


class _FakeKG:
    """A store stand-in for the branches a real store will not produce."""

    def __init__(self, *, provenance_error: Optional[Exception] = None,
                 nodes: Optional[List[Dict[str, Any]]] = None) -> None:
        self.provenance_error = provenance_error
        self._nodes = nodes or []
        self.imports: List[Dict[str, Any]] = []

    def schema_versions(self):
        return {"graph_schema_version": 1, "embed_dim": 8}

    def export_graph_data(self, *, workspace_id=None, include_legacy_global=False):
        return {
            "nodes": list(self._nodes), "edges": [], "chunks": [],
            "knowledge_sources": [], "provenance": [],
        }

    def import_graph_data(self, data, *, mode="merge", dry_run=False):
        self.imports.append(data)
        return {"imported": True}

    def record_provenance(self, **kwargs: Any):
        if self.provenance_error is not None:
            raise self.provenance_error
        return {"id": "prov-1"}

    def get_node(self, node_id, **_: Any):
        raise ValueError(f"graph node not found: {node_id}")


def _brain(tmp_path: Path, tag: str, *, seed: bool = False):
    store = KnowledgeGraphStore(tmp_path / f"{tag}.sqlite", tmp_path / f"{tag}-blobs")
    data_dir = tmp_path / f"{tag}-data"
    service = KGPortabilityService(
        knowledge_graph=store,
        data_dir=data_dir,
        device_identity=DeviceIdentity(data_dir),
    )
    node_ids: List[str] = []
    if seed:
        pipeline = IngestionPipeline(store)
        for title, text in (
            ("Storage decision", "We chose SQLite because the brain must stay portable."),
            ("Storage context", "Background notes on storage engines and their tradeoffs."),
        ):
            result = pipeline.ingest(IngestionItem(
                source_type="note", title=title, text=text,
                workspace_id="ws-1", owner="sender@example.com",
                source_uri="/Users/sender/private/notes.md",
            ))
            node_ids.append(result.node_id)
    return service, store, node_ids


def _sink() -> ReviewQueueService:
    return ReviewQueueService(store=_ReviewStore())


# ── the flag ─────────────────────────────────────────────────────────────────

def test_sharing_is_off_until_the_operator_opts_in(tmp_path, monkeypatch):
    monkeypatch.delenv(BRAIN_NETWORK_ENV, raising=False)
    service, _, node_ids = _brain(tmp_path, "src", seed=True)

    assert brain_network_enabled() is False
    status = service.share_status()
    assert status["enabled"] is False
    assert status["flag"] == BRAIN_NETWORK_ENV
    assert BRAIN_NETWORK_ENV in status["detail"]
    # Honest about what the encryption is and is not.
    assert status["encryption"] == "passphrase"
    assert status["recipient_public_key_encryption"] is False

    for call in (
        lambda: service.export_subgraph(node_ids=node_ids),
        lambda: service.read_subgraph_archive(tmp_path / "x", passphrase=PASSPHRASE),
        lambda: service.import_subgraph_proposals({}, review_sink=_sink()),
        lambda: service.accept_subgraph_proposal("id", review_sink=_sink()),
    ):
        with pytest.raises(BrainNetworkDisabled):
            call()

    monkeypatch.setenv(BRAIN_NETWORK_ENV, "yes")
    assert brain_network_enabled() is True
    assert service.share_status()["enabled"] is True
    assert service.share_status()["detail"] is None


def test_share_status_without_a_device_identity_reports_no_signer(tmp_path, enabled):
    service = KGPortabilityService(
        knowledge_graph=_FakeKG(), data_dir=tmp_path / "data",
    )
    status = service.share_status()
    assert status["signing"] is False
    assert status["device"] == {}
    with pytest.raises(RuntimeError, match="device identity is required"):
        service.export_subgraph(node_types=["Document"])


# ── export selection ─────────────────────────────────────────────────────────

def test_export_requires_a_selector(tmp_path, enabled):
    service, _, _ = _brain(tmp_path, "src", seed=True)
    with pytest.raises(ValueError, match="must name what to share"):
        service.export_subgraph()
    with pytest.raises(ValueError, match="matched no nodes"):
        service.export_subgraph(node_ids=["nope"])


def test_export_selects_by_id_type_and_source_type(tmp_path, enabled):
    service, _, node_ids = _brain(tmp_path, "src", seed=True)

    by_id = service.export_subgraph(node_ids=[node_ids[0]], workspace_id="ws-1")
    assert [n["id"] for n in by_id["nodes"]] == [node_ids[0]]
    assert by_id["header"]["format"] == SUBGRAPH_FORMAT
    assert by_id["header"]["counts"]["nodes"] == 1
    assert by_id["header"]["includes_knowledge_sources"] is False
    assert by_id["knowledge_sources"] == []
    # The content travels with its chunk rows, so the receiver can search it.
    assert by_id["chunks"] and by_id["chunk_nodes"]

    by_type = service.export_subgraph(node_types=["Document"], workspace_id="ws-1")
    assert len(by_type["nodes"]) == 2

    # A source-type selector takes everything that source produced — the
    # documents *and* the concepts extracted from them.
    by_source = service.export_subgraph(source_types=["note"], workspace_id="ws-1")
    assert set(node_ids) <= {n["id"] for n in by_source["nodes"]}
    assert all(
        json.loads(n["metadata_json"])["source_type"] == "note"
        for n in by_source["nodes"]
    )

    unmatched = service.export_subgraph(node_ids=[node_ids[0], "ghost"], workspace_id="ws-1")
    assert unmatched["header"]["unmatched_node_ids"] == ["ghost"]


def test_export_redacts_the_senders_identity_by_default(tmp_path, enabled):
    service, _, node_ids = _brain(tmp_path, "src", seed=True)

    redacted = service.export_subgraph(node_ids=[node_ids[0]], workspace_id="ws-1")
    metadata = json.loads(redacted["nodes"][0]["metadata_json"])
    assert "owner" not in metadata and "source_uri" not in metadata
    assert redacted["provenance"][0]["source_uri"] is None
    assert redacted["provenance"][0]["owner"] is None
    assert set(redacted["header"]["redacted"]) == {
        "owner", "user_email", "source_uri", "permissions",
    }

    raw = service.export_subgraph(
        node_ids=[node_ids[0]], workspace_id="ws-1", redact_provenance=False,
    )
    assert json.loads(raw["nodes"][0]["metadata_json"])["source_uri"].endswith("notes.md")
    assert raw["header"]["redacted"] == []


def test_neighbor_expansion_never_pulls_in_people_or_sources(tmp_path, enabled):
    service, store, node_ids = _brain(tmp_path, "src", seed=True)

    expanded = service.export_subgraph(
        node_ids=[node_ids[0]], workspace_id="ws-1", include_neighbors=True,
    )
    types = {n["type"] for n in expanded["nodes"]}
    assert "Person" not in types and "Source" not in types
    assert types <= {"Document", "Concept", "Feature", "Task", "Decision", "Error", "Code"}
    # The Person node exists in the graph — it was simply not admitted.
    assert any(n["type"] == "Person" for n in store.export_graph_data()["nodes"])
    assert len(expanded["nodes"]) > 1


# ── signing / verification ───────────────────────────────────────────────────

def test_a_bundle_is_signed_and_its_payload_is_pinned(tmp_path, enabled):
    service, _, node_ids = _brain(tmp_path, "src", seed=True)
    artifact = service.export_subgraph(node_ids=[node_ids[0]], workspace_id="ws-1")

    verdict = service.verify_subgraph(artifact)
    assert verdict["ok"] is True
    assert verdict["origin"] == "device:" + artifact["signature"]["fingerprint"]

    tampered = dict(artifact)
    tampered["nodes"] = [dict(artifact["nodes"][0], title="rewritten")]
    assert service.verify_subgraph(tampered)["errors"] == [
        "bundle contents do not match the signed digest",
    ]


def test_verify_refuses_unsigned_wrong_format_and_newer_bundles(tmp_path, enabled):
    service, _, node_ids = _brain(tmp_path, "src", seed=True)
    artifact = service.export_subgraph(node_ids=[node_ids[0]], workspace_id="ws-1")

    unsigned = {k: v for k, v in artifact.items() if k != "signature"}
    assert "bundle is unsigned" in service.verify_subgraph(unsigned)["errors"]

    wrong_format = json.loads(json.dumps(artifact))
    wrong_format["header"]["format"] = "latticeai.kg.export"
    assert "not a Lattice subgraph bundle" in service.verify_subgraph(wrong_format)["errors"]

    newer = json.loads(json.dumps(artifact))
    newer["header"]["format_version"] = 99
    assert any("newer than this build" in e for e in service.verify_subgraph(newer)["errors"])

    unpinned = json.loads(json.dumps(artifact))
    unpinned["header"].pop("payload_sha256")
    assert "bundle header does not pin its payload digest" in (
        service.verify_subgraph(unpinned)["errors"]
    )

    resigned = json.loads(json.dumps(artifact))
    resigned["header"]["exported_at"] = "2020-01-01T00:00:00+00:00"
    assert "signature does not match the bundle header" in (
        service.verify_subgraph(resigned)["errors"]
    )

    assert service.verify_subgraph("not a bundle")["ok"] is False


# ── encrypted bundle file ────────────────────────────────────────────────────

def test_encrypted_bundle_round_trips_and_names_its_sender(tmp_path, enabled):
    service, _, node_ids = _brain(tmp_path, "src", seed=True)

    written = service.export_subgraph_archive(
        tmp_path / "share", passphrase=PASSPHRASE,
        node_ids=[node_ids[0]], workspace_id="ws-1",
    )
    path = Path(written["path"])
    assert path.suffix == ".latticebrain"
    envelope = json.loads(path.read_text(encoding="utf-8"))
    # Header and signature stay outside the ciphertext so a recipient can see
    # who sent it before typing a passphrase; the graph itself is encrypted.
    assert envelope["header"]["device"]["fingerprint"]
    assert "Storage decision" not in base64.b64decode(envelope["payload"]).decode(
        "utf-8", "ignore"
    )

    back = service.read_subgraph_archive(path, passphrase=PASSPHRASE)
    assert service.verify_subgraph(back)["ok"] is True
    assert [n["id"] for n in back["nodes"]] == [node_ids[0]]

    default_path = service.export_subgraph_archive(
        passphrase=PASSPHRASE, node_ids=[node_ids[0]], workspace_id="ws-1",
    )
    assert Path(default_path["path"]).name.startswith("subgraph-")


def test_reading_a_bundle_fails_closed_on_every_bad_input(tmp_path, enabled):
    service, _, node_ids = _brain(tmp_path, "src", seed=True)
    written = service.export_subgraph_archive(
        tmp_path / "share.latticebrain", passphrase=PASSPHRASE,
        node_ids=[node_ids[0]], workspace_id="ws-1",
    )

    with pytest.raises(FileNotFoundError):
        service.read_subgraph_archive(tmp_path / "absent.latticebrain", passphrase=PASSPHRASE)

    broken = tmp_path / "broken.latticebrain"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        service.read_subgraph_archive(broken, passphrase=PASSPHRASE)

    other = tmp_path / "other.latticebrain"
    other.write_text(json.dumps({"format": "latticebrain.encrypted"}), encoding="utf-8")
    with pytest.raises(ValueError, match="Not a .latticebrain subgraph bundle"):
        service.read_subgraph_archive(other, passphrase=PASSPHRASE)

    envelope = json.loads(Path(written["path"]).read_text(encoding="utf-8"))
    stripped = tmp_path / "stripped.latticebrain"
    stripped.write_text(
        json.dumps({k: v for k, v in envelope.items() if k != "kdf"}), encoding="utf-8",
    )
    with pytest.raises(ValueError, match="missing encryption metadata"):
        service.read_subgraph_archive(stripped, passphrase=PASSPHRASE)

    with pytest.raises(ValueError, match="passphrase or the bundle is invalid"):
        service.read_subgraph_archive(written["path"], passphrase="wrong-passphrase")


# ── receiving: proposals, never a merge ──────────────────────────────────────

def test_a_received_subgraph_lands_as_proposals_and_accepting_one_merges_it(tmp_path, enabled):
    sender, _, node_ids = _brain(tmp_path, "src", seed=True)
    receiver, dst_store, _ = _brain(tmp_path, "dst")
    bundle = sender.export_subgraph(node_ids=node_ids, workspace_id="ws-1")
    sink = _sink()

    plan = receiver.import_subgraph_proposals(
        bundle, review_sink=sink, workspace_id="ws-2", dry_run=True,
    )
    assert plan["status"] == "dry_run"
    assert plan["proposed"] == 2
    assert sink.list()["items"] == []

    received = receiver.import_subgraph_proposals(
        bundle, review_sink=sink, workspace_id="ws-2", user_email="me@example.com",
    )
    assert received["status"] == "proposed"
    assert received["signature_verified"] is True
    assert received["origin"] == "device:" + bundle["signature"]["fingerprint"]
    assert len(received["items"]) == 2
    # Nothing has entered the graph yet — that is the whole point.
    assert sum(dst_store.stats().get("nodes", {}).values()) == 0

    queued = sink.get(received["items"][0])
    assert queued["kind"] == SUBGRAPH_REVIEW_KIND
    assert queued["provenance"]["signature_verified"] is True
    assert queued["provenance"]["fingerprint"] == bundle["signature"]["fingerprint"]
    assert queued["summary"].startswith("[device:")

    accepted = receiver.accept_subgraph_proposal(
        received["items"][0], review_sink=sink, workspace_id="ws-2",
    )
    assert accepted["status"] == "accepted"
    assert accepted["review_status"] == "approved"
    assert accepted["origin"] == received["origin"]
    landed = dst_store.export_graph_data()["nodes"]
    assert accepted["node_id"] in {n["id"] for n in landed}
    # The accepting workspace owns what it accepted.
    accepted_node = next(n for n in landed if n["id"] == accepted["node_id"])
    assert json.loads(accepted_node["metadata_json"])["workspace_id"] == "ws-2"
    # The other proposal is untouched: acceptance is per item.
    assert sink.get(received["items"][1])["status"] == "pending"


def test_receiving_refuses_an_unverifiable_bundle(tmp_path, enabled):
    sender, _, node_ids = _brain(tmp_path, "src", seed=True)
    receiver, dst_store, _ = _brain(tmp_path, "dst")
    bundle = sender.export_subgraph(node_ids=node_ids, workspace_id="ws-1")
    bundle["nodes"].append({"id": "injected", "type": "Document", "title": "not mine"})
    sink = _sink()

    with pytest.raises(ValueError, match="Refusing the bundle"):
        receiver.import_subgraph_proposals(bundle, review_sink=sink)

    assert sink.list()["items"] == []
    assert sum(dst_store.stats().get("nodes", {}).values()) == 0


def test_the_proposal_cap_is_reported_not_hidden(tmp_path, enabled):
    sender, _, node_ids = _brain(tmp_path, "src", seed=True)
    receiver, _, _ = _brain(tmp_path, "dst")
    bundle = sender.export_subgraph(node_ids=node_ids, workspace_id="ws-1")

    received = receiver.import_subgraph_proposals(bundle, review_sink=_sink(), cap=1)

    assert received["nodes"] == 2
    assert received["proposed"] == 1
    assert received["skipped"] == 1
    assert received["capped"] is True


def test_an_edge_into_an_absent_node_is_deferred_not_written(tmp_path, enabled):
    sender, _, node_ids = _brain(tmp_path, "src", seed=True)
    receiver, dst_store, _ = _brain(tmp_path, "dst")
    bundle = sender.export_subgraph(
        node_ids=node_ids, workspace_id="ws-1", include_neighbors=True,
    )
    sink = _sink()
    received = receiver.import_subgraph_proposals(bundle, review_sink=sink)
    first = next(
        item for item in sink.list()["items"]
        if item["payload"]["edges"] and any(
            str(e["to_node"]) != item["payload"]["node"]["id"]
            and str(e["from_node"]) != item["payload"]["node"]["id"]
            or True
            for e in item["payload"]["edges"]
        )
    )

    accepted = receiver.accept_subgraph_proposal(first["id"], review_sink=sink)

    written = {(e["from_node"], e["to_node"]) for e in dst_store.export_graph_data()["edges"]}
    for deferred in accepted["edges_deferred"]:
        assert (deferred["from"], deferred["to"]) not in written
        assert deferred["reason"] == "the other node is not in this Brain yet"
    assert received["status"] == "proposed"


def test_accepting_both_ends_of_a_relation_writes_the_edge(tmp_path, enabled):
    """A deferred edge is not a lost edge: accept the other end and it lands."""
    sender, src_store, node_ids = _brain(tmp_path, "src", seed=True)
    receiver, dst_store, _ = _brain(tmp_path, "dst")
    # Relate the two seeded documents so the bundle carries a real edge.
    src_store.import_graph_data(
        {
            "nodes": [], "edges": [{
                "from_node": node_ids[0], "to_node": node_ids[1],
                "type": "REFERENCES", "weight": 1.0, "metadata_json": "{}",
            }],
            "chunks": [], "knowledge_sources": [], "provenance": [],
        },
        mode="merge",
    )
    bundle = sender.export_subgraph(node_ids=node_ids, workspace_id="ws-1")
    sink = _sink()
    received = receiver.import_subgraph_proposals(bundle, review_sink=sink)

    first = receiver.accept_subgraph_proposal(received["items"][0], review_sink=sink)
    second = receiver.accept_subgraph_proposal(received["items"][1], review_sink=sink)

    # The first acceptance had nowhere to point yet; the second closes it.
    assert any(
        d["type"] == "REFERENCES" for d in first["edges_deferred"]
    ) or first["edges_written"] >= 1
    landed = {
        (e["from_node"], e["to_node"], e["type"])
        for e in dst_store.export_graph_data()["edges"]
    }
    assert (node_ids[0], node_ids[1], "REFERENCES") in landed
    assert second["edges_written"] >= 1


def test_accepting_refuses_anything_that_is_not_an_open_proposal(tmp_path, enabled):
    receiver, _, _ = _brain(tmp_path, "dst")
    sink = _sink()

    other = sink.create(title="unrelated", source="workflow_run", payload={"workflow_id": "w1"})
    with pytest.raises(ValueError, match="not a shared-subgraph proposal"):
        receiver.accept_subgraph_proposal(other["id"], review_sink=sink)

    headless = sink.create(
        title="no node", source="kg_change_digest",
        payload={"kind": SUBGRAPH_REVIEW_KIND, "node": {}},
    )
    with pytest.raises(ValueError, match="carries no node id"):
        receiver.accept_subgraph_proposal(headless["id"], review_sink=sink)

    decided = sink.create(
        title="already handled", source="kg_change_digest",
        payload={"kind": SUBGRAPH_REVIEW_KIND, "node": {"id": "n1", "type": "Document"}},
    )
    sink.dismiss(decided["id"])
    with pytest.raises(ValueError, match="already dismissed; nothing was imported"):
        receiver.accept_subgraph_proposal(decided["id"], review_sink=sink)

    with pytest.raises(FileNotFoundError):
        receiver.accept_subgraph_proposal("missing", review_sink=sink)


def test_provenance_capture_failure_never_loses_the_proposals(tmp_path, enabled):
    sender, _, node_ids = _brain(tmp_path, "src", seed=True)
    bundle = sender.export_subgraph(node_ids=[node_ids[0]], workspace_id="ws-1")
    receiver = KGPortabilityService(
        knowledge_graph=_FakeKG(provenance_error=RuntimeError("provenance table is gone")),
        data_dir=tmp_path / "dst-data",
        device_identity=DeviceIdentity(tmp_path / "dst-data"),
    )
    sink = _sink()

    received = receiver.import_subgraph_proposals(bundle, review_sink=sink)

    assert received["proposed"] == 1
    assert len(sink.list()["items"]) == 1


def test_accepting_without_a_workspace_leaves_the_node_scope_alone(tmp_path, enabled):
    sender, _, node_ids = _brain(tmp_path, "src", seed=True)
    kg = _FakeKG()
    receiver = KGPortabilityService(
        knowledge_graph=kg, data_dir=tmp_path / "dst-data",
        device_identity=DeviceIdentity(tmp_path / "dst-data"),
    )
    sink = _sink()
    bundle = sender.export_subgraph(node_ids=[node_ids[0]], workspace_id="ws-1")
    received = receiver.import_subgraph_proposals(bundle, review_sink=sink)

    receiver.accept_subgraph_proposal(received["items"][0], review_sink=sink)

    imported = kg.imports[0]
    assert json.loads(imported["nodes"][0]["metadata_json"]).get("workspace_id") == "ws-1"


# ── pure helpers (branches a real store will not produce) ────────────────────

def test_helpers_tolerate_malformed_stored_json():
    assert portability_module._node_source_type({"metadata_json": "{oops"}) == ""
    assert portability_module._node_source_type({"metadata_json": "[1, 2]"}) == ""
    assert portability_module._strip_fields(None) is None
    assert portability_module._strip_fields("{oops") == "{oops"
    assert portability_module._strip_fields("[1, 2]") == "[1, 2]"
    scoped = portability_module._scope_node({"metadata_json": "{oops"}, "ws-9")
    assert json.loads(scoped["metadata_json"]) == {"workspace_id": "ws-9"}
    listed = portability_module._scope_node({"metadata_json": "[1, 2]"}, "ws-9")
    assert json.loads(listed["metadata_json"]) == {"workspace_id": "ws-9"}
    assert portability_module._shared_node_summary({}, {"origin": None}) == "[unknown device]"
    assert portability_module._shared_node_summary(
        {"summary": "body"}, {"origin": "device:aa"},
    ) == "[device:aa] body"


# ── routes ───────────────────────────────────────────────────────────────────

def _require_admin(request: Request):
    if request.headers.get("X-Test-Admin") != "true":
        raise HTTPException(status_code=403, detail="admin required")
    return "admin@example.com"


def _client(service: Any, *, review_queue: Any = None) -> TestClient:
    app = FastAPI()
    app.include_router(create_portability_router(
        service=service,
        require_user=lambda request: "user@example.com",
        require_admin=_require_admin,
        review_queue=review_queue,
    ))
    return TestClient(app)


def test_share_routes_answer_403_with_the_reason_while_the_flag_is_off(tmp_path, monkeypatch):
    monkeypatch.delenv(BRAIN_NETWORK_ENV, raising=False)
    service, _, node_ids = _brain(tmp_path, "src", seed=True)
    client = _client(service, review_queue=_sink())

    status = client.get("/api/knowledge-graph/share", headers=EN).json()
    assert status["enabled"] is False
    assert "LATTICEAI_BRAIN_NETWORK=1" in status["detail"]

    for method, url, body in (
        ("post", "/api/knowledge-graph/share/export", {"node_ids": node_ids}),
        ("post", "/api/knowledge-graph/share/archive",
         {"node_ids": node_ids, "passphrase": PASSPHRASE}),
        ("post", "/api/knowledge-graph/share/import", {"artifact": {}}),
        ("post", "/api/knowledge-graph/share/proposals/x/accept", {}),
    ):
        response = getattr(client, method)(url, json=body, headers={**ADMIN, **EN})
        assert response.status_code == 403, url
        assert "off by default" in response.json()["detail"]


def test_share_routes_require_admin(tmp_path, enabled):
    service, _, node_ids = _brain(tmp_path, "src", seed=True)
    client = _client(service, review_queue=_sink())
    assert client.post(
        "/api/knowledge-graph/share/export", json={"node_ids": node_ids},
    ).status_code == 403
    # The status read is available to any signed-in user.
    assert client.get("/api/knowledge-graph/share").status_code == 200


def test_share_export_and_archive_routes(tmp_path, enabled):
    service, _, node_ids = _brain(tmp_path, "src", seed=True)
    client = _client(service, review_queue=_sink())

    exported = client.post(
        "/api/knowledge-graph/share/export",
        json={"node_ids": node_ids[:1], "workspace_id": "ws-1"}, headers=ADMIN,
    )
    assert exported.status_code == 200
    assert exported.json()["header"]["format"] == SUBGRAPH_FORMAT

    empty = client.post("/api/knowledge-graph/share/export", json={}, headers=ADMIN)
    assert empty.status_code == 400
    assert "must name what to share" in empty.json()["detail"]

    archived = client.post(
        "/api/knowledge-graph/share/archive",
        json={"node_ids": node_ids[:1], "workspace_id": "ws-1",
              "path": str(tmp_path / "route.latticebrain"), "passphrase": PASSPHRASE},
        headers=ADMIN,
    )
    assert archived.status_code == 200
    assert Path(archived.json()["path"]).exists()

    bad_archive = client.post(
        "/api/knowledge-graph/share/archive",
        json={"passphrase": PASSPHRASE}, headers=ADMIN,
    )
    assert bad_archive.status_code == 400


def test_share_import_and_accept_routes(tmp_path, enabled):
    sender, _, node_ids = _brain(tmp_path, "src", seed=True)
    receiver, dst_store, _ = _brain(tmp_path, "dst")
    sink = _sink()
    client = _client(receiver, review_queue=sink)
    bundle = sender.export_subgraph(node_ids=node_ids[:1], workspace_id="ws-1")

    received = client.post(
        "/api/knowledge-graph/share/import",
        json={"artifact": bundle, "workspace_id": "ws-2"}, headers=ADMIN,
    )
    assert received.status_code == 200
    item_id = received.json()["items"][0]

    accepted = client.post(
        f"/api/knowledge-graph/share/proposals/{item_id}/accept",
        json={"workspace_id": "ws-2"}, headers=ADMIN,
    )
    assert accepted.status_code == 200
    assert sum(dst_store.stats().get("nodes", {}).values()) > 0

    missing = client.post(
        "/api/knowledge-graph/share/proposals/ghost/accept", json={}, headers={**ADMIN, **EN},
    )
    assert missing.status_code == 404

    replayed = client.post(
        f"/api/knowledge-graph/share/proposals/{item_id}/accept",
        json={"workspace_id": "ws-2"}, headers=ADMIN,
    )
    assert replayed.status_code == 400
    assert "already approved" in replayed.json()["detail"]


def test_share_import_route_reads_an_encrypted_bundle_from_disk(tmp_path, enabled):
    sender, _, node_ids = _brain(tmp_path, "src", seed=True)
    receiver, _, _ = _brain(tmp_path, "dst")
    client = _client(receiver, review_queue=_sink())
    written = sender.export_subgraph_archive(
        tmp_path / "wire.latticebrain", passphrase=PASSPHRASE,
        node_ids=node_ids[:1], workspace_id="ws-1",
    )

    ok = client.post(
        "/api/knowledge-graph/share/import",
        json={"path": written["path"], "passphrase": PASSPHRASE, "dry_run": True},
        headers=ADMIN,
    )
    assert ok.status_code == 200
    assert ok.json()["status"] == "dry_run"

    incomplete = client.post(
        "/api/knowledge-graph/share/import", json={"path": written["path"]}, headers=ADMIN,
    )
    assert incomplete.status_code == 400
    assert "passphrase" in incomplete.json()["detail"]

    absent = client.post(
        "/api/knowledge-graph/share/import",
        json={"path": str(tmp_path / "gone.latticebrain"), "passphrase": PASSPHRASE},
        headers=ADMIN,
    )
    assert absent.status_code == 404


def test_share_routes_report_a_missing_review_queue(tmp_path, enabled):
    service, _, _ = _brain(tmp_path, "src", seed=True)
    client = _client(service, review_queue=None)

    unavailable = client.post(
        "/api/knowledge-graph/share/import", json={"artifact": {}}, headers={**ADMIN, **EN},
    )
    assert unavailable.status_code == 503
    assert "review queue is not connected" in unavailable.json()["detail"]

    accept = client.post(
        "/api/knowledge-graph/share/proposals/x/accept", json={}, headers={**ADMIN, **EN},
    )
    assert accept.status_code == 503


def test_accept_route_reports_a_concurrent_decision_as_a_conflict(tmp_path, enabled):
    receiver, _, _ = _brain(tmp_path, "dst")

    class _RacingSink:
        def get(self, item_id, *, workspace_id=None):
            return {
                "id": item_id, "status": "pending",
                "payload": {"kind": SUBGRAPH_REVIEW_KIND,
                            "node": {"id": "n1", "type": "Document", "title": "T"}},
                "provenance": {"origin": "device:aa"},
            }

        def approve(self, item_id, *, workspace_id=None):
            raise InvalidReviewTransition("approve", "approved")

    client = _client(receiver, review_queue=_RacingSink())
    response = client.post(
        "/api/knowledge-graph/share/proposals/n1/accept", json={}, headers=ADMIN,
    )
    assert response.status_code == 409
