"""v11.2.0 F4 — the remaining 11.1.0 limitations, closed one at a time.

Each block below names the limitation it retires, because "we fixed it" is only
meaningful next to what the old text said:

* *"The vault bridge is a manual one-shot sync."* — watch mode, opt-in.
* *"there is no bulk accept"* — bulk approve/dismiss with per-item verdicts.
* *"the agent runtime holds its executor prompt as a fixed string"* — the
  Self-Model port.
* *"no UI supplies one yet, so that path is API-only"* — the search API can
  produce the image query vector itself, when a shared-space model exists.
* the ``kgv2_edges`` COALESCE observation carried since 11.0.1.
* the ANN freshness count, which had no index to answer from.
* the automation confidence floor no real suggestion could ever fall below.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.graph.store import KnowledgeGraphStore  # noqa: E402
from lattice_brain.ingestion import IngestionItem, IngestionPipeline  # noqa: E402
from latticeai.api.review_queue import (  # noqa: E402
    BULK_ACTION_CAP,
    create_review_queue_router,
)
from latticeai.services.automation_intelligence import (  # noqa: E402
    _MIN_SUGGESTION_CONFIDENCE,
    _question_confidence,
)
from latticeai.services.folder_watch import (  # noqa: E402
    VAULT_WATCH_GATE,
    WATCH_KIND_VAULT,
    FolderWatchService,
)
from latticeai.services.review_queue import (  # noqa: E402
    InvalidReviewTransition,
    ReviewQueueService,
)
from latticeai.services.search_service import (  # noqa: E402
    IMAGE_QUERY_FUSION_GATE,
    SearchService,
)


# ── the vault watch (11.1.0: "a manual one-shot sync") ───────────────────────
class _RecordingBridge:
    def __init__(self, *, status: str = "ok", boom: bool = False) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.status = status
        self.boom = boom

    def sync(self, path, **kwargs):
        self.calls.append({"path": path, **kwargs})
        if self.boom:
            raise RuntimeError("vault went away")
        return {
            "status": self.status,
            "ingested": 2,
            "duplicate": 1,
            "failed": 0,
            "errors": [],
            "edges": {"status": "written", "references": 3},
        }


@pytest.fixture(autouse=True)
def _gates_are_off_unless_a_test_says_otherwise():
    VAULT_WATCH_GATE.reset()
    IMAGE_QUERY_FUSION_GATE.reset()
    yield
    VAULT_WATCH_GATE.reset()
    IMAGE_QUERY_FUSION_GATE.reset()


def _watch_service(tmp_path, bridge=None):
    return FolderWatchService(
        pipeline=SimpleNamespace(ingest=lambda *_a, **_k: None),
        config_path=tmp_path / "watch.json",
        vault_bridge=bridge,
    )


def test_a_vault_watch_refuses_until_it_is_turned_on(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("첫 노트", encoding="utf-8")
    service = _watch_service(tmp_path, _RecordingBridge())

    refused = service.enable(vault, kind=WATCH_KIND_VAULT)
    assert refused["status"] == "disabled"
    assert "off by default" in refused["detail"]
    assert service.status()["vault_watch"]["enabled"] is False
    assert service.status()["vault_watch"]["bridge_wired"] is True

    assert service.enable(vault, kind="nonsense")["status"] == "failed"

    VAULT_WATCH_GATE.set(True)
    allowed = service.enable(vault, kind=WATCH_KIND_VAULT)
    assert allowed["status"] == "ok"
    assert allowed["watch"]["kind"] == WATCH_KIND_VAULT


def test_a_watched_vault_re_syncs_only_when_it_actually_changed(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("첫 노트", encoding="utf-8")
    bridge = _RecordingBridge()
    service = _watch_service(tmp_path, bridge)
    VAULT_WATCH_GATE.set(True)
    watch_id = service.enable(vault, kind=WATCH_KIND_VAULT, owner="me@local")["watch"]["id"]

    quiet = service.scan_once(watch_id)
    assert quiet["synced"] is False
    assert quiet["detail"] == "vault unchanged since the last scan"
    assert bridge.calls == []

    (vault / "second.md").write_text("[[첫 노트]] 를 참고", encoding="utf-8")
    moved = service.scan_once(watch_id)
    assert moved["synced"] is True
    assert (moved["ingested"], moved["duplicate"]) == (2, 1)
    assert moved["edges"]["references"] == 3
    assert bridge.calls[0]["owner"] == "me@local"

    # A whole-vault sync is what makes the link edges real — that is why the
    # 11.1.0 note refused to schedule a per-file incremental pass.
    assert bridge.calls[0]["path"] == str(vault.resolve())


def test_turning_the_vault_gate_back_off_stops_the_work_immediately(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("첫 노트", encoding="utf-8")
    bridge = _RecordingBridge()
    service = _watch_service(tmp_path, bridge)
    VAULT_WATCH_GATE.set(True)
    watch_id = service.enable(vault, kind=WATCH_KIND_VAULT)["watch"]["id"]
    (vault / "second.md").write_text("변경", encoding="utf-8")

    VAULT_WATCH_GATE.set(False)
    stopped = service.scan_once(watch_id)
    assert stopped["status"] == "disabled" and bridge.calls == []


def test_a_vault_watch_without_a_bridge_or_with_a_broken_one_says_so(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("첫 노트", encoding="utf-8")
    VAULT_WATCH_GATE.set(True)

    bare = _watch_service(tmp_path)
    watch_id = bare.enable(vault, kind=WATCH_KIND_VAULT)["watch"]["id"]
    (vault / "second.md").write_text("변경", encoding="utf-8")
    result = bare.scan_once(watch_id)
    assert result["status"] == "failed" and "no vault bridge" in result["detail"]

    broken = _watch_service(tmp_path / "b", _RecordingBridge(boom=True))
    broken_id = broken.enable(vault, kind=WATCH_KIND_VAULT)["watch"]["id"]
    (vault / "third.md").write_text("또 변경", encoding="utf-8")
    outcome = broken.scan_once(broken_id)
    assert outcome["status"] == "failed" and "vault went away" in outcome["detail"]


def test_a_partial_vault_sync_is_reported_as_partial(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("첫 노트", encoding="utf-8")
    service = _watch_service(tmp_path, _RecordingBridge(status="partial"))
    VAULT_WATCH_GATE.set(True)
    watch_id = service.enable(vault, kind=WATCH_KIND_VAULT)["watch"]["id"]
    (vault / "second.md").write_text("변경", encoding="utf-8")
    assert service.scan_once(watch_id)["status"] == "partial"


def test_the_watch_route_answers_403_with_the_reason_while_the_gate_is_off(tmp_path):
    from latticeai.api.local_files import create_local_files_router
    from latticeai.api.permissions import create_permissions_router

    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("첫 노트", encoding="utf-8")
    watch = _watch_service(tmp_path, _RecordingBridge())

    permissions_router, gateway = create_permissions_router(
        config=SimpleNamespace(
            discord_permission_webhook="",
            discord_bot_token="",
            discord_permission_channel="",
            permission_monitor_secret="",
            port=4825,
        ),
        data_dir=tmp_path / "perm",
        require_user=lambda request: "me@local",
        require_admin=lambda request: "me@local",
        get_current_user=lambda request: "me@local",
    )
    app = FastAPI()
    app.include_router(permissions_router)
    app.include_router(create_local_files_router(
        require_user=lambda request: "me@local",
        tool_response=lambda fn, *args: fn(*args),
        permission_gateway=gateway,
        knowledge_graph=None,
        require_graph=lambda: None,
        static_dir=tmp_path / "static",
        local_kg_watcher=None,
        ingestion_pipeline=SimpleNamespace(available=lambda: True),
        folder_watch=watch,
    ))
    client = TestClient(app)

    token = gateway.local_permission_response(str(vault), "read", "me@local")["approval_token"]
    assert client.post(f"/permissions/approve/{token}").status_code == 200
    body = {
        "path": str(vault), "kind": "vault",
        "approved": True, "approval_token": token,
    }

    refused = client.post("/api/ingestion/watch", json=body)
    assert refused.status_code == 403
    assert "off by default" in refused.json()["detail"]

    VAULT_WATCH_GATE.set(True)
    allowed = client.post("/api/ingestion/watch", json=body)
    assert allowed.status_code == 200
    assert allowed.json()["watch"]["kind"] == "vault"


def test_an_ordinary_folder_watch_is_untouched_by_any_of_this(tmp_path):
    folder = tmp_path / "folder"
    folder.mkdir()
    ingested: List[Any] = []
    service = FolderWatchService(
        pipeline=SimpleNamespace(
            ingest=lambda item, **_: ingested.append(item)
            or SimpleNamespace(status="ok", duplicate=False, detail=None),
        ),
        config_path=tmp_path / "watch.json",
    )
    watch_id = service.enable(folder)["watch"]["id"]
    (folder / "new.md").write_text("새 파일", encoding="utf-8")
    result = service.scan_once(watch_id)
    assert result["kind"] == "folder" and result["ingested"] == 1
    assert "synced" not in result


# ── bulk review actions (11.1.0: "there is no bulk accept") ──────────────────
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


def _review_client(change_proposals: Any = None):
    service = ReviewQueueService(store=_ReviewStore())
    audit: List[str] = []
    app = FastAPI()
    app.include_router(create_review_queue_router(
        service=service,
        require_user=lambda request: "me@local",
        gate_read=lambda request: None,
        gate_write=lambda request: None,
        run_review_item=lambda *_a, **_k: {"ok": True},
        append_audit_event=lambda action, **_k: audit.append(action),
        change_proposals=change_proposals,
    ))
    return TestClient(app), service, audit


def _seed(service, count: int, *, source: str = "workflow_run") -> List[str]:
    return [
        service.create(title=f"제안 {i}", source=source, kind="suggestion")["id"]
        for i in range(count)
    ]


def test_bulk_approve_reports_every_item_individually(monkeypatch):
    client, service, audit = _review_client()
    ids = _seed(service, 3)
    service.dismiss(ids[2])

    response = client.post(
        "/automation/reviews/bulk/approve",
        json={"ids": [ids[0], ids[1], ids[2], "ghost", "  "]},
    )
    body = response.json()
    assert response.status_code == 200
    assert (body["requested"], body["succeeded"], body["failed"]) == (4, 2, 2)
    verdicts = {row["id"]: row["status"] for row in body["results"]}
    assert verdicts[ids[0]] == verdicts[ids[1]] == "ok"
    assert verdicts[ids[2]] == "conflict"      # already decided → not re-decided
    assert verdicts["ghost"] == "not_found"
    # N decisions, N audit events: the trail records what actually happened.
    assert audit.count("review_item_approve") == 2


def test_bulk_dismiss_carries_the_reason_and_the_scope_is_never_implicit():
    client, service, _ = _review_client()
    ids = _seed(service, 2)

    empty = client.post("/automation/reviews/bulk/dismiss", json={"ids": []})
    assert empty.status_code == 422

    too_many = client.post(
        "/automation/reviews/bulk/dismiss",
        json={"ids": [f"id-{i}" for i in range(BULK_ACTION_CAP + 1)]},
    )
    assert too_many.status_code == 422
    assert str(BULK_ACTION_CAP) in too_many.json()["detail"]

    done = client.post(
        "/automation/reviews/bulk/dismiss", json={"ids": ids, "reason": "중복 제안"},
    )
    assert done.json()["succeeded"] == 2
    assert service.get(ids[0])["provenance"]["dismiss_reason"] == "중복 제안"


def test_bulk_approve_applies_staged_change_proposals_like_the_single_route():
    applied: List[str] = []

    class _Proposals:
        def approve_and_apply(self, item_id, *, user_email=None, workspace_id=None):
            applied.append(item_id)
            return {"item": {"id": item_id, "status": "approved"}}

    client, service, _ = _review_client(_Proposals())
    staged = _seed(service, 1, source="change_proposal")
    plain = _seed(service, 1)

    body = client.post(
        "/automation/reviews/bulk/approve", json={"ids": staged + plain},
    ).json()
    assert body["succeeded"] == 2
    assert applied == staged  # the staged content really was applied


def test_a_proposal_that_cannot_be_applied_fails_only_itself():
    from latticeai.services.change_proposals import ProposalConflictError

    class _Conflicting:
        def approve_and_apply(self, item_id, **_k):
            raise ProposalConflictError(
                reason="file_changed", path="docs/plan.md", kind="edit",
            )

    client, service, _ = _review_client(_Conflicting())
    staged = _seed(service, 1, source="change_proposal")
    plain = _seed(service, 1)

    body = client.post(
        "/automation/reviews/bulk/approve", json={"ids": staged + plain},
    ).json()
    verdicts = {row["id"]: row["status"] for row in body["results"]}
    assert verdicts[staged[0]] == "conflict"
    assert verdicts[plain[0]] == "ok"


def test_every_failure_shape_a_bulk_call_can_meet_is_named():
    from fastapi import HTTPException

    class _Awkward:
        def __init__(self) -> None:
            self.calls = 0

        def approve_and_apply(self, item_id, **_k):
            self.calls += 1
            if self.calls == 1:
                raise KeyError(item_id)
            if self.calls == 2:
                raise HTTPException(status_code=500, detail="disk full")
            if self.calls == 3:
                raise HTTPException(status_code=409, detail="already applied")
            raise ValueError("not a proposal")

    client, service, _ = _review_client(_Awkward())
    ids = _seed(service, 4, source="change_proposal")
    body = client.post("/automation/reviews/bulk/approve", json={"ids": ids}).json()
    assert [row["status"] for row in body["results"]] == [
        "not_found", "failed", "conflict", "failed",
    ]
    assert body["succeeded"] == 0


def test_an_already_decided_change_proposal_is_a_conflict_not_a_reapplication():
    class _Proposals:
        def approve_and_apply(self, item_id, **_k):  # pragma: no cover - never reached
            raise AssertionError("must not apply an already-approved proposal")

    client, service, _ = _review_client(_Proposals())
    staged = _seed(service, 1, source="change_proposal")
    service.approve(staged[0])
    body = client.post("/automation/reviews/bulk/approve", json={"ids": staged}).json()
    assert body["results"][0]["status"] == "conflict"
    with pytest.raises(InvalidReviewTransition):
        service.approve(staged[0])


# ── the kgv2_edges COALESCE observation (carried since 11.0.1) ───────────────
def test_a_canonical_edge_no_longer_reads_back_with_an_empty_type(tmp_path):
    """``COALESCE('', type)`` returned ``''``; ``NULLIF`` is why it no longer does."""
    store = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    with store._connect() as conn:
        store._upsert_node(conn, "a", "Document", "A")
        store._upsert_node(conn, "b", "Document", "B")
        store._upsert_edge(conn, "a", "b", "MENTIONS")
        store._upsert_edge(conn, "a", "b", "포함함")  # a legacy label, normalized
        rows = {
            row["type"]
            for row in conn.execute("SELECT type FROM kgv2_edges WHERE from_node='a'")
        }
        raw = {
            row["legacy_type"]
            for row in conn.execute("SELECT legacy_type FROM edges_v2 WHERE source='a'")
        }
    assert "" not in rows
    assert rows == {"MENTIONS", "CONTAINS"}
    # The write-side sentinel is unchanged: '' still means "no legacy label",
    # which is what keeps the UNIQUE dedupe key working.
    assert raw == {""}


def test_rows_written_by_an_older_build_are_normalized_idempotently(tmp_path):
    db = tmp_path / "kg.sqlite"
    store = KnowledgeGraphStore(db, tmp_path / "blobs")
    with store._connect() as conn:
        store._upsert_node(conn, "a", "Document", "A")
        store._upsert_node(conn, "b", "Document", "B")
        store._upsert_edge(conn, "a", "b", "MENTIONS")
        # An older build stored the canonical value in *both* columns, which
        # split one relation across two rows under the dedupe key.
        conn.execute(
            "INSERT INTO edges_v2(id, source, target, type, legacy_type, weight,"
            " confidence, evidence, metadata, created_by, created_at)"
            " VALUES ('edge:legacy','a','b','REFERENCES','REFERENCES',1.0,1.0,'[]','{}','legacy','t')"
        )
        conn.execute(
            "UPDATE nodes_v2 SET legacy_type='' WHERE id='a'"
        )

    reopened = KnowledgeGraphStore(db, tmp_path / "blobs")
    with reopened._connect() as conn:
        edge = conn.execute(
            "SELECT legacy_type FROM edges_v2 WHERE id='edge:legacy'"
        ).fetchone()
        node = conn.execute("SELECT legacy_type FROM nodes_v2 WHERE id='a'").fetchone()
        view = conn.execute(
            "SELECT type FROM kgv2_edges WHERE id='edge:legacy'"
        ).fetchone()
    assert edge["legacy_type"] == ""
    assert node["legacy_type"] is None
    assert view["type"] == "REFERENCES"

    # Re-running matches nothing — the migration is idempotent by construction.
    with reopened._connect() as conn:
        report = reopened._normalize_v2_legacy_types(conn)
    assert report == {"edges": 0, "nodes": 0, "collisions": 0}


def test_a_migration_that_cannot_run_leaves_the_data_alone(tmp_path):
    store = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")

    class _Broken:
        def execute(self, *_a, **_k):
            raise sqlite3.OperationalError("no such table")

    assert store._normalize_v2_legacy_types(_Broken()) == {
        "edges": 0, "nodes": 0, "collisions": 0,
    }


def test_a_redundant_row_that_collides_is_counted_not_deleted(tmp_path):
    db = tmp_path / "kg.sqlite"
    store = KnowledgeGraphStore(db, tmp_path / "blobs")
    with store._connect() as conn:
        store._upsert_node(conn, "a", "Document", "A")
        store._upsert_node(conn, "b", "Document", "B")
        store._upsert_edge(conn, "a", "b", "MENTIONS")   # legacy_type=''
        conn.execute(
            "INSERT INTO edges_v2(id, source, target, type, legacy_type, weight,"
            " confidence, evidence, metadata, created_by, created_at)"
            " VALUES ('edge:dupe','a','b','MENTIONS','MENTIONS',1.0,1.0,'[]','{}','legacy','t')"
        )
        report = store._normalize_v2_legacy_types(conn)
        survived = conn.execute(
            "SELECT legacy_type FROM edges_v2 WHERE id='edge:dupe'"
        ).fetchone()
    assert report["collisions"] == 1 and report["edges"] == 0
    # Left in place: the fixed view reads it correctly either way, and deleting
    # an edge is not a migration's business.
    assert survived["legacy_type"] == "MENTIONS"


# ── the ANN freshness count ──────────────────────────────────────────────────
def test_the_freshness_fingerprint_has_a_covering_index_to_answer_from(tmp_path):
    store = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    with store._connect() as conn:
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' "
            "AND name='idx_vector_embeddings_model_dim_indexed'"
        ).fetchone()
        plan = conn.execute(
            "EXPLAIN QUERY PLAN SELECT COUNT(*), MAX(indexed_at) FROM vector_embeddings "
            "WHERE embedding_model=? AND embedding_dim=?",
            ("m", 8),
        ).fetchall()
    assert sql is not None
    assert "embedding_model, embedding_dim, indexed_at" in sql["sql"]
    assert any("idx_vector_embeddings_model_dim_indexed" in str(row[3]) for row in plan)


# ── the automation confidence floor that nothing could fall below ────────────
def test_the_suppression_floor_is_reachable_again():
    """The cheapest real question used to score 0.475 against a 0.35 floor."""
    ungrounded, factors = _question_confidence(2, ["질문 하나"], None, 0)
    assert ungrounded < _MIN_SUGGESTION_CONFIDENCE
    assert factors["kg_grounded"] is False

    unknown, unknown_factors = _question_confidence(2, ["질문 하나"], None, None)
    assert unknown >= _MIN_SUGGESTION_CONFIDENCE
    assert unknown_factors["kg_grounded"] is None

    grounded, grounded_factors = _question_confidence(2, ["질문 하나"], None, 3)
    assert grounded > unknown > ungrounded
    assert grounded_factors["kg_grounded"] is True


def test_an_ungrounded_question_is_actually_held_back_end_to_end():
    from latticeai.services.automation_intelligence import AutomationIntelligenceService

    class _Graph:
        def search(self, *_a, **_k):
            return {"matches": []}

    class _Conversations:
        def history(self, **_k):
            return [
                {"role": "user", "content": "저 파일 어디 있어?", "timestamp": "2026-08-01T09:00:00"},
                {"role": "user", "content": "저 파일 어디 있어?", "timestamp": "2026-08-02T09:00:00"},
            ]

    class _Store:
        def list_workflows(self, **_k):
            return {"workflows": []}

    service = AutomationIntelligenceService(
        conversation_store=_Conversations(),
        knowledge_graph=_Graph(),
        store=_Store(),
        enable_graph=True,
    )
    report = service.suggestions(user_email="me@local")
    assert report["quality"]["suppressed_low_confidence"] >= 1
    assert all(item["kind"] != "recurring_question" for item in report["suggestions"])


# ── the Self-Model port into the agent loop ──────────────────────────────────
def _runtime(**deps_overrides):
    from latticeai.core.agent import AgentDeps, AgentRunContext, SingleAgentRuntime

    async def _generate(*_a, **_k):  # pragma: no cover - never called here
        return ""

    deps = AgentDeps(
        generate_as=_generate,
        generate=_generate,
        execute_tool=lambda *_a, **_k: {},
        policy_for=lambda *_a, **_k: {},
        risk_level=lambda *_a: "low",
        check_role=lambda *_a: None,
        tool_governance={},
        file_create_actions=frozenset(),
        recent_chat_context=lambda **_k: "(none)",
        clear_history=lambda *_a: {},
        knowledge_save=lambda *_a, **_k: {},
        audit=lambda *_a, **_k: None,
        planner_prompt="PLAN",
        executor_prompt="EXEC",
        critic_prompt="CRITIC",
        memory_updater_prompt="MEM",
        agent_root=Path("."),
        **deps_overrides,
    )
    ctx = AgentRunContext()
    ctx.plan = {"steps": []}
    request = SimpleNamespace(message="파일 하나 만들어줘", conversation_id="c1")
    return SingleAgentRuntime(deps), ctx, request


def test_without_the_port_the_prompt_is_byte_identical_to_before():
    runtime, ctx, request = _runtime()
    prompt = runtime._executor_context(ctx, request, "ko", "me@local", None)
    assert "ABOUT THE USER" not in prompt
    assert prompt.startswith("EXEC")


def test_a_summary_reaches_the_executor_prompt_and_is_read_once_per_run():
    calls: List[Dict[str, Any]] = []

    def _resolver(*, user_email=None, workspace_id=None):
        calls.append({"user_email": user_email, "workspace_id": workspace_id})
        return "사용자에 대해 확인된 사실:\n- 선호: 한국어 답변"

    runtime, ctx, request = _runtime(self_model_summary=_resolver)
    first = runtime._executor_context(ctx, request, "ko", "me@local", "ws-1")
    second = runtime._executor_context(ctx, request, "ko", "me@local", "ws-1")

    assert "ABOUT THE USER" in first and "한국어 답변" in first
    assert first == second
    # The executor prompt is rebuilt every turn; the profile is read once.
    assert calls == [{"user_email": "me@local", "workspace_id": "ws-1"}]


def test_the_port_accepts_a_plain_string_and_an_unscoped_resolver():
    runtime, ctx, request = _runtime(self_model_summary="- 습관: 아침에 정리")
    assert "아침에 정리" in runtime._executor_context(ctx, request, "ko", "me", None)

    runtime, ctx, request = _runtime(self_model_summary=lambda: "- 결정: 로컬 우선")
    assert "로컬 우선" in runtime._executor_context(ctx, request, "ko", "me", None)


def test_a_profile_that_cannot_be_read_never_costs_the_run():
    def _broken(**_k):
        raise RuntimeError("graph is down")

    runtime, ctx, request = _runtime(self_model_summary=_broken)
    prompt = runtime._executor_context(ctx, request, "ko", "me@local", None)
    assert "ABOUT THE USER" not in prompt

    # An empty profile is the same as no port at all.
    blank, blank_ctx, blank_request = _runtime(self_model_summary=lambda **_k: "   ")
    assert "ABOUT THE USER" not in blank._executor_context(
        blank_ctx, blank_request, "ko", "me@local", None,
    )


def test_the_composition_root_port_answers_empty_without_a_graph(tmp_path):
    from latticeai.runtime.build_phases import self_model_port

    assert self_model_port(lambda: None)(user_email="me@local") == ""

    store = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    port = self_model_port(lambda: store)
    # A Brain that knows nothing about its owner injects nothing.
    assert port(user_email="me@local", workspace_id="ws-1") == ""

    from lattice_brain.self_model import upsert_self_model_fact

    upsert_self_model_fact(
        store, kind="preference", text="한국어 답변을 선호한다", workspace_id="ws-1",
    )
    assert "선호" in port(user_email="me@local", workspace_id="ws-1")


def test_the_wiring_supplies_the_port_all_the_way_down():
    from latticeai.runtime.chat_wiring import build_chat_agent_runtime_from_context

    seen: Dict[str, Any] = {}

    def _fake_build(**kwargs):
        seen.update(kwargs)
        return "runtime"

    built = build_chat_agent_runtime_from_context(
        build_agent_runtime=_fake_build,
        model_router=None,
        execute_tool=None,
        recent_chat_context=None,
        clear_history=None,
        knowledge_save=None,
        audit=None,
        hooks=None,
        brain_memory=None,
        self_model_summary="profile",
    )
    assert built == "runtime"
    assert seen["self_model_summary"] == "profile"


# ── text query → image space (11.1.0: "no UI supplies one yet") ──────────────
def _image_brain(tmp_path):
    from lattice_brain.graph.image_vectors import record_image_vector

    store = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    pipeline = IngestionPipeline(store)
    result = pipeline.ingest(
        IngestionItem(
            source_type="note",
            title="화이트보드",
            text="회의실 화이트보드 사진에 대한 메모입니다.",
        ),
        user_email="me@local",
    )
    record_image_vector(
        store, node_id=result.node_id, vector=[1.0, 0.0], model_id="clip:2",
        space="shared",
    )
    return store, result.node_id


def test_a_typed_question_can_reach_the_image_index_when_a_model_allows_it(tmp_path):
    store, node_id = _image_brain(tmp_path)
    service = SearchService(
        graph_store=store, image_query_embedder=lambda _q: [1.0, 0.0],
    )
    IMAGE_QUERY_FUSION_GATE.set(True)

    report = service.hybrid_search("화이트보드", limit=5, image_fusion=True)
    fusion = report["multimodal"]["image_fusion"]
    assert fusion["applied"] is True and fusion["fused"] == 1
    match = next(m for m in report["matches"] if m["id"] == node_id)
    assert match["source_scores"]["image"] == 1.0
    assert match["rank"] == 1

    # Not asking keeps the response exactly what 11.1.0 returned.
    assert "multimodal" not in service.hybrid_search("화이트보드", limit=5)


def test_asking_without_a_shared_space_model_says_so_instead_of_pretending(tmp_path):
    store, _ = _image_brain(tmp_path)
    service = SearchService(graph_store=store)
    IMAGE_QUERY_FUSION_GATE.set(True)

    report = service.hybrid_search("화이트보드", limit=5, image_fusion=True)
    fusion = report["multimodal"]["image_fusion"]
    assert fusion["applied"] is False
    assert "no shared-space vision model" in fusion["detail"]
    assert service.image_query_status()["available"] is False


def test_the_install_gate_is_reported_rather_than_silently_ignored(tmp_path):
    store, _ = _image_brain(tmp_path)
    service = SearchService(
        graph_store=store, image_query_embedder=lambda _q: [1.0, 0.0],
    )
    report = service.hybrid_search("화이트보드", limit=5, image_fusion=True)
    assert "automatic image fusion is off" in report["multimodal"]["image_fusion"]["detail"]
    assert service.image_query_status()["gate"]["enabled"] is False


def test_a_broken_encoder_or_index_never_costs_the_text_answer(tmp_path, monkeypatch):
    store, _ = _image_brain(tmp_path)
    IMAGE_QUERY_FUSION_GATE.set(True)

    def _explode(_query):
        raise RuntimeError("model not loaded")

    broken = SearchService(graph_store=store, image_query_embedder=_explode)
    report = broken.hybrid_search("화이트보드", limit=5, image_fusion=True)
    assert report["matches"], "the text ranking must survive"
    assert "could not embed the query" in report["multimodal"]["image_fusion"]["detail"]

    blank = SearchService(graph_store=store, image_query_embedder=lambda _q: [])
    assert "empty query vector" in blank.hybrid_search(
        "화이트보드", limit=5, image_fusion=True,
    )["multimodal"]["image_fusion"]["detail"]

    monkeypatch.setattr(
        "lattice_brain.graph.image_vectors.image_similarity_search",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("index gone")),
    )
    hostile = SearchService(graph_store=store, image_query_embedder=lambda _q: [1.0, 0.0])
    fusion = hostile.hybrid_search("화이트보드", limit=5, image_fusion=True)
    assert "image index unavailable" in fusion["multimodal"]["image_fusion"]["detail"]
    assert fusion["matches"]


def test_the_port_is_taken_from_the_store_the_app_already_wired(tmp_path):
    from lattice_brain.multimodal import MultimodalPorts

    store, _ = _image_brain(tmp_path)
    store.multimodal_ports = MultimodalPorts(
        text_to_image_embedder=lambda _q: [1.0, 0.0], vision_space="shared",
    )
    service = SearchService(graph_store=store)
    IMAGE_QUERY_FUSION_GATE.set(True)
    assert service.image_query_status()["available"] is True
    assert service.hybrid_search(
        "화이트보드", limit=5, image_fusion=True,
    )["multimodal"]["image_fusion"]["applied"] is True


def test_an_image_hit_outside_the_text_ranking_leaves_it_alone(tmp_path):
    """Late fusion only ever *re-scores* what the text channels already found."""
    from lattice_brain.graph.image_vectors import record_image_vector

    store = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    IngestionPipeline(store).ingest(
        IngestionItem(source_type="note", title="화이트보드", text="회의실 메모입니다."),
        user_email="me@local",
    )
    # The only picture in the image index belongs to a node this ranking will
    # never contain.
    record_image_vector(
        store, node_id="image:not-in-this-ranking", vector=[0.0, 1.0],
        model_id="clip:2", space="shared",
    )
    IMAGE_QUERY_FUSION_GATE.set(True)
    service = SearchService(graph_store=store, image_query_embedder=lambda _q: [0.0, 1.0])

    report = service.hybrid_search("화이트보드", limit=5, image_fusion=True)
    fusion = report["multimodal"]["image_fusion"]
    assert fusion["applied"] is True and fusion["candidates"] == 1
    assert fusion["fused"] == 0
    assert all("image" not in (m.get("source_scores") or {}) for m in report["matches"])


def test_the_shared_space_port_is_only_built_for_a_model_that_can_do_it():
    from latticeai.services.multimodal_ports import text_to_image_port

    class _ImageOnly:
        shares_text_space = False

    class _Shared:
        shares_text_space = True

        def embed_batch(self, texts):
            return [[float(len(texts[0])), 0.0]]

    class _Silent(_Shared):
        def embed_batch(self, texts):
            return []

    assert text_to_image_port(None) is None
    assert text_to_image_port(_ImageOnly()) is None
    assert text_to_image_port(_Shared())("abc") == [3.0, 0.0]
    assert text_to_image_port(_Silent())("abc") == []


def test_the_route_exposes_both_the_switch_and_the_honest_status(tmp_path):
    from latticeai.api.search import create_search_router

    store, _ = _image_brain(tmp_path)
    service = SearchService(graph_store=store)
    app = FastAPI()
    app.include_router(create_search_router(
        service=service, require_user=lambda request: "me@local",
    ))
    client = TestClient(app)

    status = client.get("/api/search/image-query").json()
    assert status["available"] is False and status["gate"]["enabled"] is False

    posted = client.post(
        "/api/search/hybrid", json={"query": "화이트보드", "image_fusion": True},
    ).json()
    assert posted["multimodal"]["image_fusion"]["requested"] is True

    got = client.get("/api/search/hybrid?q=화이트보드&image_fusion=true").json()
    assert "multimodal" in got
    assert "multimodal" not in client.get("/api/search/hybrid?q=화이트보드").json()


# ── test hygiene: the real HOME is never written to ──────────────────────────
def test_the_suite_runs_against_a_sandbox_home():
    """The guard that keeps ``~/.ltcai`` and ``~/.ltcai-brain`` out of a test run."""
    import os

    sandbox = os.environ.get("LATTICEAI_TEST_SANDBOX_HOME")
    assert sandbox, "tests/conftest.py must install a sandbox HOME before collection"
    assert str(Path.home()) == sandbox
    # Modules that resolve their storage at import time landed inside it.
    from latticeai.services import p_reinforce

    assert str(p_reinforce.BRAIN_DIR).startswith(sandbox)


def _unused(request: Request) -> None:  # pragma: no cover - import guard
    return None
