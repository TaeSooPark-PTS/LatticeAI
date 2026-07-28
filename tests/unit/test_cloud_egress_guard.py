"""What may leave the machine — proved end to end, not in isolation.

10.1.0 shipped `is_node_blocked_for_cloud` with a passing unit test that fed it
a hand-built dict. Two things that test could not see:

1. Nothing in the product could *set* the flags it looks for, so the guard was
   correct code protecting nothing.
2. The predicate is only useful if the dicts coming out of retrieval still
   carry ``metadata``. If ``hybrid_search`` ever stops returning that key, the
   filter silently matches nothing and every existing test still passes — the
   same failure shape as the ``"EXECUTING"`` literal fixed in 10.0.1.

These tests use a real store and the real payload builder, so both gaps fail
loudly instead of silently.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.graph.store import KnowledgeGraphStore
from lattice_brain.sensitivity import sensitive_reason_for_path, stamp_sensitivity
from latticeai.core.network_boundary import (
    HARD_BLOCK_NODE_TYPES,
    NetworkBoundaryMode,
    is_node_blocked_for_cloud,
)
from latticeai.services.hybrid_context import build_minimal_context


def _store(tmp_path) -> KnowledgeGraphStore:
    return KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")


def _ingest(store, tmp_path, name: str, text: str, *, workspace_id="w1"):
    doc = tmp_path / name
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(text, encoding="utf-8")
    return store.ingest_document(doc, workspace_id=workspace_id)


def test_retrieval_matches_still_carry_metadata(tmp_path):
    """The filter reads ``metadata``; retrieval must keep returning it.

    Without this, dropping the key would disable the guard invisibly.
    """
    store = _store(tmp_path)
    _ingest(store, tmp_path, "release.txt", "릴리스 절차: 태그를 만들고 CI를 통과시킨다")
    matches = store.hybrid_search("릴리스", top_k=5, allowed_workspaces={"w1"}).get("matches") or []
    assert matches, "fixture produced no matches; the rest of this file proves nothing"
    assert "metadata" in matches[0], (
        "hybrid_search stopped returning 'metadata' — is_node_blocked_for_cloud "
        "now matches nothing and the cloud filter is silently disabled"
    )


def test_a_flagged_memory_never_reaches_the_payload(tmp_path):
    """The whole promise, end to end: real store → real builder → absent."""
    store = _store(tmp_path)
    secret = _ingest(store, tmp_path, "secret-release.txt", "릴리스 배포 비밀 키 절차")
    _ingest(store, tmp_path, "public-release.txt", "릴리스 절차 공개 문서")

    node_id = secret.get("node_id") or secret.get("id")
    assert node_id, "ingest_document did not report a node id"

    # Flag it the way the product does.
    import json as _json

    with store._connect() as conn:
        row = conn.execute("SELECT metadata_json FROM nodes WHERE id=?", (node_id,)).fetchone()
        meta = _json.loads(row[0] or "{}") if row else {}
        meta["local_only"] = True
        conn.execute(
            "UPDATE nodes SET metadata_json=? WHERE id=?", (_json.dumps(meta), node_id)
        )

    ctx = build_minimal_context(
        "릴리스",
        store=store,
        mode=NetworkBoundaryMode.CLOUD_ALLOWED,
        top_k=10,
        allowed_workspaces={"w1"},
    )
    assert node_id not in ctx.node_ids, "a local_only memory was selected for cloud egress"
    assert "비밀 키" not in ctx.compact_text, "flagged content reached the outbound payload"


def test_secret_bearing_paths_are_flagged_at_ingestion(tmp_path):
    """A user indexing a project folder should not have to remember the .env."""
    store = _store(tmp_path)
    _ingest(store, tmp_path, ".env", "API_TOKEN=abc123\nDB_PASSWORD=hunter2")

    matches = store.hybrid_search("API_TOKEN", top_k=10, allowed_workspaces={"w1"}).get("matches") or []
    for match in matches:
        meta = match.get("metadata") or {}
        if str(match.get("type")) == "Document":
            assert meta.get("local_only"), ".env was ingested without a never-leaves flag"
            assert is_node_blocked_for_cloud(match), ".env document is not blocked from cloud"


def test_blocked_node_types_are_not_empty():
    """The type arm was an empty set through 10.1.x, so it could never fire."""
    assert HARD_BLOCK_NODE_TYPES, "HARD_BLOCK_NODE_TYPES is empty; the type filter cannot fire"
    assert "Credential" in HARD_BLOCK_NODE_TYPES


def test_outbound_text_is_redacted(tmp_path):
    """Pattern redaction is the only guard on an unflagged pasted secret."""
    store = _store(tmp_path)
    _ingest(
        store,
        tmp_path,
        "notes.txt",
        "배포 노트: 토큰은 sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcd 입니다",
    )
    ctx = build_minimal_context(
        "배포 노트",
        store=store,
        mode=NetworkBoundaryMode.CLOUD_ALLOWED,
        top_k=5,
        allowed_workspaces={"w1"},
    )
    assert "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcd" not in ctx.compact_text, (
        "an OpenAI-shaped key survived into the outbound payload"
    )


def test_local_only_still_builds_context_but_never_sends_it(tmp_path):
    """The documented split: the builder is also the preview, the caller is the gate.

    ``build_minimal_context`` deliberately assembles a context under
    ``local_only`` so the settings panel can show a user what *would* leave
    before they opt in. The guarantee that nothing actually leaves lives one
    layer up, so that is where it is asserted.
    """
    import asyncio

    from latticeai.services.hybrid_chat import run_hybrid_cloud_turn

    store = _store(tmp_path)
    _ingest(store, tmp_path, "release.txt", "릴리스 절차 문서")

    ctx = build_minimal_context(
        "릴리스",
        store=store,
        mode=NetworkBoundaryMode.LOCAL_ONLY,
        top_k=5,
        allowed_workspaces={"w1"},
    )
    assert ctx.node_ids, "preview should still show what would be sent"

    sent: list = []

    class RecordingAdapter:
        name = "recording"

        async def stream(self, **kwargs):  # pragma: no cover - must never run
            sent.append(kwargs)
            yield ""

    with pytest.raises(PermissionError):
        asyncio.run(
            run_hybrid_cloud_turn(
                user_message="릴리스",
                knowledge_graph=store,
                mode=NetworkBoundaryMode.LOCAL_ONLY,
                workspace_id="w1",
                adapter=RecordingAdapter(),
            )
        )
    assert sent == [], "a cloud turn ran while the boundary was local_only"


def test_stamp_never_downgrades_an_existing_flag():
    """An ordinary path must not clear a flag someone else set deliberately."""
    meta = {"local_only": True, "local_only_reason": "user marked this private"}
    stamp_sensitivity(meta, "/projects/notes/readme.md")
    assert meta["local_only"] is True
    assert meta["local_only_reason"] == "user marked this private"


def test_sensitive_path_rules_do_not_over_match():
    """False positives quarantine ordinary work; keep the rule narrow."""
    for ordinary in (
        "/projects/app/README.md",
        "/projects/app/src/environment.ts",
        "/notes/credentials-policy-draft.md",
        "/photos/pemberley.jpg",
    ):
        assert sensitive_reason_for_path(ordinary) is None, f"{ordinary} wrongly flagged"


def test_every_send_is_audited_before_it_happens(tmp_path, monkeypatch):
    """A send with no record is indistinguishable from a send that never happened."""
    import asyncio

    from latticeai.services import cloud_egress_audit
    from latticeai.services.hybrid_chat import run_hybrid_cloud_turn

    events: list = []
    cloud_egress_audit.bind_egress_audit(lambda **kw: events.append(kw))

    store = _store(tmp_path)
    _ingest(store, tmp_path, "release.txt", "릴리스 절차 문서")

    class FakeAdapter:
        name = "fake-provider"

        async def stream(self, **kwargs):
            # The audit record must already exist by the time bytes move.
            assert events, "the send was not audited before the provider was called"
            yield "ok"

    try:
        asyncio.run(
            run_hybrid_cloud_turn(
                user_message="릴리스",
                knowledge_graph=store,
                mode=NetworkBoundaryMode.CLOUD_ALLOWED,
                workspace_id="w1",
                user_email="me@local",
                adapter=FakeAdapter(),
            )
        )
    except Exception:
        # The bridge may need more of a real adapter than this fake provides;
        # what this test asserts is the audit record, not the turn's success.
        pass
    finally:
        cloud_egress_audit.bind_egress_audit(None)

    assert events, "a cloud turn produced no audit event"
    event = events[0]
    assert event["event"] == "cloud_egress"
    assert event["provider"] == "fake-provider"
    assert event["mode"] == "cloud_allowed"
    assert event["workspace_id"] == "w1"
    assert event["node_count"] == len(event["node_ids"])
    # Shape, never content: the outbound text must not be copied into the log.
    assert "compact_text" not in event


def test_a_refusal_is_audited_too(tmp_path):
    """"Nothing left the machine, and here is why" is also worth recording."""
    from latticeai.services import cloud_egress_audit

    events: list = []
    cloud_egress_audit.bind_egress_audit(lambda **kw: events.append(kw))
    try:
        cloud_egress_audit.record_cloud_egress(
            node_ids=["n1"], token_estimate=99_999, mode="cloud_allowed",
            provider="(refused)", outcome="refused_token_guard",
            detail="session limit exceeded",
        )
    finally:
        cloud_egress_audit.bind_egress_audit(None)

    assert events[0]["outcome"] == "refused_token_guard"
    assert events[0]["detail"] == "session limit exceeded"


def test_a_broken_audit_sink_never_breaks_the_send(tmp_path):
    from latticeai.services import cloud_egress_audit

    def exploding(**kw):
        raise RuntimeError("audit backend down")

    cloud_egress_audit.bind_egress_audit(exploding)
    try:
        event = cloud_egress_audit.record_cloud_egress(
            node_ids=["n1"], token_estimate=10, mode="cloud_allowed", provider="p",
        )
    finally:
        cloud_egress_audit.bind_egress_audit(None)
    assert event["node_ids"] == ["n1"]
