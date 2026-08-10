"""wpb05 — the service-layer branch directions that had never run.

Every test here drives one arc that line coverage cannot see: a guard whose
*false* side was never taken, a loop entered zero times, an ``elif`` chain that
falls off the end. The subjects are ordinary service objects, so each one is
built with injected fakes and asserted on its return value rather than on "it
did not raise" — a branch that is reached but produces the wrong answer is the
failure this suite exists to catch.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from latticeai.core.network_boundary import NetworkBoundaryMode
from latticeai.services import cloud_token_guard, hybrid_chat, triggers
from latticeai.services import model_capability_registry as mcr
from latticeai.services.automation_execution import dry_run_report
from latticeai.services.change_proposals import ChangeProposalService
from latticeai.services.chat_service import ChatService
from latticeai.services.command_center import CommandCenterService
from latticeai.services.evidence_actions import EvidenceActionService
from latticeai.services.funnel_metrics import FunnelMetricsService
from latticeai.services.hybrid_context import MinimalContext
from latticeai.services.model_catalog import _model_family_version
from latticeai.services.multimodal_streaming import MultimodalStreamingBridge
from latticeai.services.review_queue import _extract_run_id, enqueue_from_automation
from latticeai.services.run_executor import RunExecutor
from latticeai.services.tool_dispatch import collect_created_files

# ── chat_service ─────────────────────────────────────────────────────────────


def _chat_service(**overrides: Any) -> ChatService:
    base: Dict[str, Any] = {
        "store": None,
        "get_history": lambda **scope: [],
        "save_to_history": lambda *a, **k: None,
        "get_history_user": lambda *a, **k: {},
    }
    base.update(overrides)
    return ChatService(**base)


def test_history_scope_skips_the_workspace_lookup_when_auth_is_off():
    """require_auth=False short-circuits the guard before the resolver runs."""
    asked: List[str] = []

    scope = _chat_service().history_scope(
        "wpb05@example.com",
        require_auth=False,
        allowed_workspaces_for=lambda email: asked.append(email) or ["ws-1"],
    )

    assert asked == [], "an unauthenticated read must not resolve workspaces"
    assert scope == {
        "user_email": None,
        "allowed_workspaces": None,
        "include_legacy_global": True,
    }


def test_history_scope_leaves_workspaces_open_when_no_resolver_is_wired():
    scope = _chat_service().history_scope(
        "wpb05@example.com", require_auth=True, allowed_workspaces_for=None,
    )

    assert scope == {
        "user_email": "wpb05@example.com",
        "allowed_workspaces": None,
        "include_legacy_global": False,
    }


def test_persist_exchange_without_a_notifier_still_writes_both_turns():
    saved: List[tuple] = []
    service = _chat_service(save_to_history=lambda role, content, **kw: saved.append((role, content, kw)))

    asyncio.run(
        service.persist_exchange(
            request_message="원본 질문",
            stored_user_message="저장된 질문",
            answer="대답",
            source="http",
            history_meta={"conversation_id": "c-1"},
            history_user={"user_email": "wpb05@example.com"},
            notify=None,
        )
    )

    assert [row[0] for row in saved] == ["user", "assistant"]
    assert saved[0][1] == "저장된 질문"
    assert saved[1][1] == "대답"
    assert saved[0][2]["conversation_id"] == "c-1"


# ── command_center ───────────────────────────────────────────────────────────


class _Conversations:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self._rows = rows

    def history(self, **_kwargs: Any) -> List[Dict[str, Any]]:
        return list(self._rows)


class _WorkflowStore:
    def __init__(self, workflows: List[Dict[str, Any]]) -> None:
        self._workflows = workflows

    def list_workflows(self, *, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        return {"workflows": list(self._workflows)}


def test_automation_section_ignores_a_workflow_that_is_neither_enabled_nor_draft():
    """A live-but-unstamped automation counts in neither bucket."""
    service = CommandCenterService(
        store=_WorkflowStore(
            [{"id": "wf-1", "name": "정리", "metadata": {"automation_state": "archived"}}]
        )
    )

    section = service._automation_section(workspace_id=None)

    assert section == {"available": True, "total": 1, "enabled": 0, "drafts": 0}


def test_search_history_keeps_messages_that_carry_no_conversation_id():
    """Loose history rows are still searchable; they just cannot be de-duped."""
    service = CommandCenterService(
        conversation_store=_Conversations(
            [
                {"role": "user", "content": "릴리스 절차", "timestamp": "2026-01-01"},
                {"role": "user", "content": "릴리스 절차", "timestamp": "2026-01-02"},
            ]
        )
    )

    matches = service._search_conversations(
        "릴리스", user_email="wpb05@example.com", workspace_id=None, limit=10,
    )

    assert len(matches) == 2, "no conversation id means no de-duplication"
    assert {row["conversation_id"] for row in matches} == {""}


# ── review_queue ─────────────────────────────────────────────────────────────


class _Sink:
    def __init__(self) -> None:
        self.created: List[Dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Dict[str, Any]:
        self.created.append(kwargs)
        return {"id": "review-1", **kwargs}


def test_enqueue_from_automation_omits_provenance_it_does_not_have():
    """No run id and no trigger info leaves provenance with the workflow alone."""
    sink = _Sink()

    item = enqueue_from_automation(
        sink=sink,
        workflow={
            "id": "wf-9",
            "name": "주간 요약",
            "nodes": [{"type": "trigger", "config": {"review_queue": True}}],
        },
        source="workflow_run",
        run_result=None,
        trigger_info=None,
    )

    assert item is not None
    assert sink.created[0]["provenance"] == {"workflow_id": "wf-9"}
    assert sink.created[0]["title"] == "주간 요약"


def test_extract_run_id_returns_nothing_for_a_result_that_is_not_a_mapping():
    assert _extract_run_id([{"run_id": "r-1"}]) is None
    assert _extract_run_id(42) is None


# ── automation_execution ─────────────────────────────────────────────────────


def test_dry_run_summary_stays_generic_when_the_workflow_creates_nothing():
    report = dry_run_report(
        {
            "id": "wf-2",
            "name": "알림만",
            "metadata": {"automation": True},
            "nodes": [
                {
                    "id": "trigger",
                    "type": "trigger",
                    "name": "Start",
                    "config": {"trigger": "manual"},
                    "next": "a1",
                },
                {
                    "id": "a1",
                    "type": "agent",
                    "name": "draft",
                    "config": {"prompt": "주간 요약"},
                    "next": None,
                },
            ],
        }
    )

    assert report["status"] == "ok"
    assert report["summary"] == (
        "1 step(s) would run locally and produce a reviewable draft; "
        "no external actions, nothing is written until you approve."
    ), "with nothing declared under `creates` the parenthetical must be absent"


# ── change_proposals ─────────────────────────────────────────────────────────


class _ReviewQueue:
    def __init__(self, item: Dict[str, Any]) -> None:
        self._item = item
        self.approved: List[str] = []

    def get(self, item_id: str, *, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        return dict(self._item)

    def approve(self, item_id: str, *, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        self.approved.append(item_id)
        return {**self._item, "status": "approved"}


def test_applying_a_delete_proposal_whose_target_is_already_gone_still_resolves(tmp_path):
    """The file vanished out of band: the review item must still close cleanly."""
    queue = _ReviewQueue(
        {
            "id": "p-1",
            "source": "change_proposal",
            "kind": "file_delete",
            "status": "pending",
            "payload": {"path": "notes/gone.md"},
        }
    )
    service = ChangeProposalService(
        review_queue=queue, resolve_path=lambda rel: tmp_path / rel,
    )

    result = service.approve_and_apply("p-1", user_email="wpb05@example.com")

    assert result == {
        "item": {**queue._item, "status": "approved"},
        "applied": True,
        "path": "notes/gone.md",
        "kind": "file_delete",
    }
    assert queue.approved == ["p-1"]
    assert not (tmp_path / "notes/gone.md").exists()


# ── cloud_token_guard ────────────────────────────────────────────────────────


def test_budget_for_hands_back_the_same_object_on_every_later_call(monkeypatch):
    monkeypatch.setattr(cloud_token_guard, "_BUDGETS", {})

    first = cloud_token_guard.budget_for("wpb05|ws-1")
    first.record(11)
    second = cloud_token_guard.budget_for("wpb05|ws-1")

    assert second is first, "the budget must survive across turns in a session"
    assert second.session_used == 11


# ── evidence_actions ─────────────────────────────────────────────────────────


def test_evidence_block_lists_a_source_that_has_no_excerpt_to_quote():
    block = EvidenceActionService()._evidence_block(
        [
            {"title": "제목만 있는 노드", "origin": "", "excerpt": "   ", "truncated": False},
            {"title": "본문 있는 노드", "origin": "회의록", "excerpt": "핵심", "truncated": True},
        ],
        "ko",
    )

    assert block.splitlines() == [
        "[근거 자료]",
        "1. 제목만 있는 노드",
        "2. 본문 있는 노드 (회의록)",
        "   핵심 …",
    ]


# ── funnel_metrics ───────────────────────────────────────────────────────────


def test_metrics_file_holding_a_json_array_is_read_as_a_fresh_state(tmp_path):
    path = tmp_path / "funnel.json"
    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")

    snapshot = FunnelMetricsService(path).snapshot()

    assert snapshot["counters"]["ingest_completions"] == 0
    assert snapshot["firsts"] == {"first_ingest_at": None, "first_value_at": None}


# ── hybrid_chat ──────────────────────────────────────────────────────────────


class _Store:
    def hybrid_search(self, query, *, top_k=20, allowed_workspaces=None, include_legacy_global=False):
        return {
            "mode": "hybrid",
            "matches": [
                {
                    "node_id": "node-1",
                    "title": "릴리스 절차",
                    "summary": "태그를 만들고 CI를 통과시킨다",
                    "type": "Decision",
                    "score": 0.9,
                    "metadata": {},
                }
            ],
        }


class _Adapter:
    provider_name = "wpb05-cloud"
    default_model = "wpb05-model"

    def stream(self, *, system, user, context, model=None):
        async def _gen():
            yield "안녕"

        return _gen()


class _RecordingChatService:
    def __init__(self) -> None:
        self.entries: List[tuple] = []

    async def persist_entry(self, role, content, *, history_meta=None, history_user=None):
        self.entries.append((role, content, history_meta, history_user))


def test_hybrid_turn_persists_without_a_notifier_wired():
    chat_service = _RecordingChatService()

    async def _drain():
        return [
            chunk
            async for chunk in hybrid_chat.stream_hybrid_cloud_turn(
                user_message="릴리스 절차 알려줘",
                knowledge_graph=_Store(),
                mode=NetworkBoundaryMode.CLOUD_ALLOWED,
                adapter=_Adapter(),
                chat_service=chat_service,
                history_meta={"conversation_id": "c-9"},
                history_user={"user_email": "wpb05@example.com"},
                notify=None,
            )
        ]

    events = asyncio.run(_drain())

    assert chat_service.entries and chat_service.entries[0][0] == "assistant"
    done = [
        json.loads(chunk[len("data: "):].strip())
        for chunk in events
        if chunk.startswith("data: ") and "hybrid_done" in chunk
    ]
    assert done and done[0]["answer"] == "안녕"


# ── model_capability_registry ────────────────────────────────────────────────


def test_engine_catalog_backfill_skips_capabilities_with_no_mlx_hint(monkeypatch):
    """The fallback exists for a registry that projected no MLX entry at all."""
    monkeypatch.setattr(
        mcr,
        "_REGISTRY",
        [SimpleNamespace(provider_hints=("openai",), to_legacy_dict=lambda: {"id": "x"})],
    )

    catalog = mcr.build_engine_model_catalog()

    assert catalog == {}, "no hint matched, so nothing is injected either"


# ── model_catalog ────────────────────────────────────────────────────────────


def test_family_version_reads_the_first_pattern_that_matches():
    assert _model_family_version({"name": "Qwen 3 Instruct"}) == ("qwen", (3,))
    # Minor versions are truncated: the filter hides older *generations*, and
    # 3.1 vs 3.6 within one generation are siblings, not a winner and a loser.
    assert _model_family_version({"id": "mlx/gemma-3.1-it"}) == ("gemma", (3,))
    assert _model_family_version({"id": "mlx/Qwen3.6-27B"}) == ("qwen", (3,))
    assert _model_family_version({"name": "phi-4"}) is None


# ── multimodal_streaming ─────────────────────────────────────────────────────


class _MediaAdapter:
    provider_name = "wpb05-media"
    default_model = "wpb05-video"

    def stream_media(self, *, prompt, context, model=None):
        async def _gen():
            yield {"media_url": "https://example.invalid/a.mp4"}
            yield {"media_url": "https://example.invalid/b.mp4", "text": "두 번째"}

        return _gen()


def test_media_events_without_a_text_note_contribute_only_their_url():
    result = asyncio.run(
        MultimodalStreamingBridge(_MediaAdapter()).run_turn(
            user_message="영상 만들어줘",
            minimal=MinimalContext(query="영상", node_ids=["n1"], compact_text="근거"),
            mode=NetworkBoundaryMode.CLOUD_ALLOWED,
            allow_multimodal=True,
        )
    )

    assert result.media_urls == [
        "https://example.invalid/a.mp4",
        "https://example.invalid/b.mp4",
    ]
    assert result.answer_text == "두 번째"


# ── run_executor ─────────────────────────────────────────────────────────────


def test_waiting_on_an_unknown_run_returns_nothing_instead_of_blocking():
    executor = RunExecutor(
        store=SimpleNamespace(),
        agent_runtime=None,
        build_workflow_runners=lambda user, scope: {},
        workspace_graph=lambda: None,
        append_audit_event=lambda *a, **k: None,
    )

    assert asyncio.run(executor.wait("run-does-not-exist")) is None


# ── tool_dispatch ────────────────────────────────────────────────────────────


def test_created_file_collection_skips_a_step_that_reported_no_path():
    files = collect_created_files(
        [
            {"action": "write_file", "result": {"bytes": 12}},
            {"action": "write_file", "result": {"path": "docs/a.md", "bytes": 3}},
        ]
    )

    assert files == [
        {"path": "docs/a.md", "filename": "a.md", "bytes": 3, "action": "write_file"}
    ]


# ── triggers ─────────────────────────────────────────────────────────────────


def test_trigger_service_runs_without_a_zoneinfo_database(monkeypatch, tmp_path: Path):
    """A Python build with no tzdata still starts; display tz is just unset."""
    monkeypatch.setattr(triggers, "ZoneInfo", None)

    service = triggers.TriggerService(
        store=SimpleNamespace(),
        run_workflow=lambda workflow_id, payload: {},
        data_dir=tmp_path,
        tz_name="Asia/Seoul",
    )

    assert service._tz is None
    assert service._tz_name == "Asia/Seoul"


@pytest.mark.parametrize("raw", ["", "   "])
def test_trigger_service_falls_back_to_utc_for_a_blank_timezone(monkeypatch, tmp_path: Path, raw):
    monkeypatch.setattr(triggers, "ZoneInfo", None)
    monkeypatch.delenv("LATTICE_TZ", raising=False)

    service = triggers.TriggerService(
        store=SimpleNamespace(),
        run_workflow=lambda workflow_id, payload: {},
        data_dir=tmp_path,
        tz_name=raw,
    )

    assert service._tz_name == "UTC"
