"""Coverage for MemoryService (wp34).

The service's contract is that it never invents a number: an unreadable backend
raises ``MemoryServiceError`` instead of reporting zero, and a degraded tier is
reported as degraded. The tests therefore break each backing store in turn and
assert the honest outcome, then walk the Memory Manager surfaces (manager /
brief focus / recall / inspect / prune / compact / rebuild / clear).
"""

from __future__ import annotations

import json

import pytest

from latticeai.services.memory_service import (
    TIERS,
    WORKSPACE_KINDS,
    MemoryService,
    MemoryServiceError,
)


class _Store:
    def __init__(self, *, memories=None, snapshots=None, search_results=None):
        self.memories = list(memories or [])
        self.snapshots = list(snapshots or [])
        self.search_results = search_results
        self.deleted = []
        self.undeletable = set()
        self.scoped_error = None
        self.all_error = None
        self.snapshot_error = None
        self.search_error = None

    def list_memories(self, **kwargs):
        if not kwargs:
            if self.all_error:
                raise self.all_error
        elif self.scoped_error:
            raise self.scoped_error
        return {"memories": list(self.memories)}

    def list_memory_snapshots(self, *, workspace_id=None, limit=200):
        if self.snapshot_error:
            raise self.snapshot_error
        return {"snapshots": list(self.snapshots)}

    def search_memories(self, query, *, user_email=None, limit=20, workspace_id=None):
        if self.search_error:
            raise self.search_error
        if self.search_results is not None:
            return {"memories": list(self.search_results)}
        return {"memories": list(self.memories)}

    def delete_memory(self, memory_id):
        if memory_id in self.undeletable:
            raise RuntimeError(f"memory {memory_id} is locked")
        self.deleted.append(memory_id)
        self.memories = [m for m in self.memories if m.get("id") != memory_id]


class _KG:
    def __init__(self, *, stats=None, index=None, matches=None, vectors=None):
        self._stats = stats if stats is not None else {"nodes": {"Concept": 2}, "edges": {"relates": 1}}
        self._index = index if index is not None else {"vector_counts": {"nodes": 2}}
        self._matches = matches or []
        self._vectors = vectors
        self.stats_error = None
        self.index_error = None
        self.search_error = None
        self.rebuild_error = None
        self.rebuilt = 0

    def stats(self):
        if self.stats_error:
            raise self.stats_error
        return self._stats

    def index_status(self):
        if self.index_error:
            raise self.index_error
        return self._index

    def search(self, query, limit, **kwargs):
        if self.search_error:
            raise self.search_error
        return {"query": query, "matches": list(self._matches)}

    def filter_scoped_nodes(self, hits, allowed, *, id_key="node_id"):
        return list(hits)

    def rebuild_vector_index(self):
        if self.rebuild_error:
            raise self.rebuild_error
        self.rebuilt += 1
        return {"reindexed": 4}


class _VectorKG(_KG):
    def vector_search(self, query, *, limit=20):
        if self._vectors is None:
            raise RuntimeError("vector index unavailable")
        return {"matches": list(self._vectors)}


def _service(tmp_path, *, store=None, kg=None, enable_graph=False, conversations=None, history=None):
    return MemoryService(
        store=store if store is not None else _Store(),
        data_dir=tmp_path,
        knowledge_graph=kg,
        enable_graph=enable_graph,
        history_file=history if history is not None else tmp_path / "chat_history.json",
        conversation_store=conversations,
    )


class _Conversations:
    def __init__(self, items=None, error=None):
        self.items = items or []
        self.error = error

    def history(self):
        if self.error:
            raise self.error
        return list(self.items)

    def size_bytes(self):
        return 128


# ── backend failures are reported, never silently zeroed ─────────────────────


def test_global_memory_backend_failure_is_reported(tmp_path):
    store = _Store()
    store.all_error = RuntimeError("sqlite is locked")
    service = _service(tmp_path, store=store)

    with pytest.raises(MemoryServiceError, match="memory backend unavailable"):
        service.manager()


def test_snapshot_backend_failure_is_reported(tmp_path):
    store = _Store()
    store.snapshot_error = RuntimeError("snapshot table missing")
    service = _service(tmp_path, store=store)

    with pytest.raises(MemoryServiceError, match="memory snapshot backend unavailable"):
        service.manager(workspace_id="w1")


def test_conversation_store_failure_is_reported(tmp_path):
    service = _service(
        tmp_path, conversations=_Conversations(error=RuntimeError("conversation db locked"))
    )

    with pytest.raises(MemoryServiceError, match="conversation backend unavailable"):
        service.manager(workspace_id="w1")


def test_unreadable_legacy_history_is_reported(tmp_path):
    history = tmp_path / "chat_history.json"
    history.write_text("{broken", encoding="utf-8")
    service = _service(tmp_path, history=history)

    with pytest.raises(MemoryServiceError, match="conversation history is unreadable"):
        service.manager(workspace_id="w1")


# ── legacy history shapes ────────────────────────────────────────────────────


def _history_service(tmp_path, payload):
    history = tmp_path / "chat_history.json"
    history.write_text(json.dumps(payload), encoding="utf-8")
    return _service(tmp_path, history=history)


def test_legacy_history_supports_the_conversations_envelope(tmp_path):
    service = _history_service(tmp_path, {"conversations": [{"id": "c1", "messages": []}]})

    assert service.inspect("conversation")["count"] == 1


def test_legacy_history_supports_a_conversation_map(tmp_path):
    service = _history_service(
        tmp_path, {"c1": {"messages": [{"content": "hi"}]}, "c2": [{"content": "raw"}]}
    )

    items = service.inspect("conversation")["items"]

    assert sorted(item["id"] for item in items) == ["c1", "c2"]
    assert {item["messages"] for item in items} == {1}


def test_legacy_history_supports_a_bare_list(tmp_path):
    service = _history_service(tmp_path, [{"id": "c1", "messages": []}])

    assert service.inspect("conversation")["count"] == 1


def test_legacy_history_of_an_unknown_shape_is_empty(tmp_path):
    service = _history_service(tmp_path, "just a string")

    assert service.inspect("conversation")["count"] == 0


def test_scoped_conversations_skip_malformed_message_lists(tmp_path):
    service = _history_service(
        tmp_path,
        [
            {"id": "broken", "messages": "not-a-list"},
            {
                "id": "c1",
                "messages": [
                    {"content": "mine", "user_email": "me@example.com", "workspace_id": "personal"},
                    {"content": "theirs", "user_email": "other@example.com"},
                ],
            },
        ],
    )

    payload = service.inspect("conversation", user_email="me@example.com")

    assert [item["id"] for item in payload["items"]] == ["c1"]
    assert payload["items"][0]["messages"] == 1


# ── knowledge graph degradation ──────────────────────────────────────────────


def test_graph_stats_and_index_failures_degrade_the_manager(tmp_path):
    kg = _KG()
    kg.stats_error = RuntimeError("graph file corrupt")
    kg.index_error = RuntimeError("vector index missing")
    service = _service(tmp_path, kg=kg, enable_graph=True)

    manager = service.manager()

    sources = {s["id"]: s for s in manager["sources"]}
    assert sources["graph"]["health"] == "unavailable"
    assert sources["vector"]["health"] == "unavailable"
    assert manager["health"] in {"ok", "degraded"}


def test_manager_reads_a_legacy_vector_index_counter(tmp_path):
    kg = _KG(index={"indexed": 7})
    service = _service(tmp_path, kg=kg, enable_graph=True)

    sources = {s["id"]: s for s in service.manager()["sources"]}

    assert sources["vector"]["count"] == 7


def test_an_empty_brain_reports_the_quiet_readiness_state(tmp_path):
    kg = _KG(stats={"nodes": {}, "edges": {}}, index={"vector_counts": {}})
    service = _service(tmp_path, kg=kg, enable_graph=True)

    readiness = service.brain_quality_summary()

    assert readiness["state"] == "quiet"
    assert readiness["score"] >= 12
    assert readiness["title_key"] == "brain.readiness.quiet"


# ── brief focus selection ────────────────────────────────────────────────────


def _focus(service, **overrides):
    kwargs = dict(
        user_email=None,
        workspace_id=None,
        recall_items=[],
        durable_items=0,
        graph_concepts=0,
        query="릴리스",
    )
    kwargs.update(overrides)
    return service._brain_brief_focus(**kwargs)


def test_focus_falls_back_to_the_newest_workspace_memory(tmp_path):
    store = _Store(memories=[{"id": "m1", "kind": "long_term", "content": "릴리스 절차 정리"}])
    service = _service(tmp_path, store=store)

    focus = _focus(service, durable_items=1)

    assert focus["kind"] == "memory"
    assert focus["title"] == "long_term"
    assert focus["detail"] == "릴리스 절차 정리"


def test_focus_falls_back_to_the_latest_conversation(tmp_path):
    service = _history_service(
        tmp_path,
        [{"id": "c1", "title": "릴리스 회의", "messages": [{"content": "결정 사항"}, {"content": "  "}]}],
    )

    focus = _focus(service, durable_items=1)

    assert focus["kind"] == "conversation"
    assert focus["title"] == "릴리스 회의"
    assert focus["detail"] == "결정 사항"


def test_focus_falls_back_to_the_graph(tmp_path):
    service = _service(tmp_path)

    focus = _focus(service, graph_concepts=5)

    assert focus["kind"] == "graph"
    assert "5" in focus["detail"]


def test_focus_reports_an_empty_brain(tmp_path):
    focus = _focus(_service(tmp_path))

    assert focus == {"kind": "empty", "title": "", "detail": "", "source": "none", "score": 0, "empty": True}


def test_conversation_history_produces_a_followup_suggestion():
    questions = MemoryService._brain_brief_suggested_questions(
        focus={"kind": "memory", "title": "릴리스"},
        has_durable_evidence=True,
        has_recall=True,
        graph_concepts=3,
        conversations=2,
    )

    assert "conversation_followup" in {q["id"] for q in questions}
    assert [q["priority"] for q in questions] == sorted(
        (q["priority"] for q in questions), reverse=True
    )


# ── latest recall query ──────────────────────────────────────────────────────


def test_latest_recall_query_skips_malformed_history_rows(tmp_path):
    service = _history_service(
        tmp_path,
        [
            {"id": "broken", "messages": "not-a-list"},
            {"id": "c1", "messages": [{"content": "마지막 질문", "workspace_id": "personal"}, "not-a-dict"]},
        ],
    )

    assert service._latest_recall_query(user_email=None, workspace_id=None) == "마지막 질문"


def test_tiers_lists_every_tier_and_workspace_kind(tmp_path):
    payload = _service(tmp_path).tiers()

    assert payload == {"tiers": list(TIERS), "workspace_kinds": list(WORKSPACE_KINDS)}


# ── recall ───────────────────────────────────────────────────────────────────


def test_recall_without_query_tokens_scores_everything_zero(tmp_path):
    store = _Store(memories=[{"id": "m1", "kind": "long_term", "content": "무엇이든", "tags": []}])
    service = _service(tmp_path, store=store)

    payload = service.recall("")

    assert payload["count"] == 1
    assert payload["results"][0]["score"] == 0.0
    assert payload["results"][0]["confidence"] == "low"


def test_recall_reports_a_failing_graph_tier_as_degraded(tmp_path):
    kg = _KG()
    kg.search_error = RuntimeError("graph search failed")
    store = _Store(memories=[{"id": "m1", "kind": "long_term", "content": "릴리스 절차", "tags": []}])
    service = _service(tmp_path, store=store, kg=kg, enable_graph=True)

    payload = service.recall("릴리스")

    assert payload["status"] == "degraded"
    assert [e["source"] for e in payload["errors"]] == ["graph"]
    assert [r["source"] for r in payload["results"]] == ["workspace"]


def test_recall_reports_a_failing_vector_tier_as_degraded(tmp_path):
    kg = _VectorKG(matches=[], vectors=None)
    service = _service(tmp_path, store=_Store(), kg=kg, enable_graph=True)

    payload = service.recall("릴리스")

    assert payload["status"] == "degraded"
    assert [e["source"] for e in payload["errors"]] == ["vector"]
    assert payload["quality_gate"]["gate"] == "lexical-evidence/v1"


def test_vector_evidence_merges_into_a_graph_hit_and_carries_its_locator(tmp_path):
    kg = _VectorKG(
        matches=[{"id": "n1", "title": "릴리스 절차", "summary": "태그 후 CI", "type": "Decision"}],
        vectors=[
            {"node_id": "n1", "score": 0.8, "metadata": {"locator": "§2 배포"}},
            {"node_id": "n2", "score": 0.0, "title": "무점수"},
        ],
    )
    service = _service(tmp_path, store=_Store(), kg=kg, enable_graph=True)

    payload = service.recall("릴리스", workspace_id="w1")

    graph_rows = [r for r in payload["results"] if r["source"] == "graph"]
    assert [r["id"] for r in graph_rows] == ["n1"], "a zero-similarity vector hit is noise"
    assert graph_rows[0]["locator"] == "§2 배포"
    assert graph_rows[0]["vector_score"] == 0.8
    assert payload["quality_gate"]["gate"] == "hybrid-evidence/v2"
    assert "semantic" in graph_rows[0]["evidence_kinds"]


# ── inspect ──────────────────────────────────────────────────────────────────


def test_inspect_walks_every_tier(tmp_path):
    store = _Store(
        memories=[{"id": "m1", "kind": "long_term", "content": "a", "workspace_id": "org:acme"}],
        snapshots=[{"id": "s1"}],
    )
    kg = _KG()
    service = _service(tmp_path, store=store, kg=kg, enable_graph=True)

    assert service.inspect("project", workspace_id="org:acme")["count"] == 1
    assert service.inspect("agent")["count"] == 1
    assert service.inspect("conversation")["count"] == 0
    assert service.inspect("graph")["available"] is True
    assert service.inspect("vector")["available"] is True

    with pytest.raises(KeyError):
        service.inspect("nonsense")


# ── prune / compact / rebuild / clear ────────────────────────────────────────


def test_prune_deduplicates_ids_and_reports_failures(tmp_path):
    store = _Store(
        memories=[
            {"id": "m1", "kind": "long_term", "content": "a"},
            {"id": "m2", "kind": "long_term", "content": "b"},
        ]
    )
    store.undeletable = {"m2"}
    service = _service(tmp_path, store=store)

    result = service.prune(ids=["m1", "m1", "m2", "forged"])

    assert result["removed"] == ["m1"]
    assert result["skipped"] == ["forged"]
    assert result["failed"][0]["id"] == "m2"
    assert result["status"] == "partial"
    assert store.deleted == ["m1"]


def test_prune_that_removes_nothing_reports_an_error_status(tmp_path):
    store = _Store(memories=[{"id": "m1", "kind": "long_term", "content": "a"}])
    store.undeletable = {"m1"}
    service = _service(tmp_path, store=store)

    result = service.prune(kind="long_term")

    assert result["removed"] == []
    assert result["status"] == "error"


def test_compact_reports_deletion_failures(tmp_path):
    store = _Store(
        memories=[
            {"id": "dup-new", "kind": "long_term", "content": "같은 내용"},
            {"id": "dup-old", "kind": "long_term", "content": "같은 내용"},
        ]
    )
    store.undeletable = {"dup-new"}
    service = _service(tmp_path, store=store)

    result = service.compact()

    assert result["compacted"] == 0
    assert result["failed"][0]["id"] == "dup-new"
    assert result["status"] == "error"


def test_rebuild_reports_disabled_ok_and_failed_targets(tmp_path):
    disabled = _service(tmp_path)
    assert disabled.rebuild()["status"] == "unavailable"

    kg = _KG()
    service = _service(tmp_path, kg=kg, enable_graph=True)
    assert service.rebuild("vector_index") == {
        "status": "ok",
        "target": "vector_index",
        "result": {"reindexed": 4},
    }

    kg.rebuild_error = RuntimeError("index write failed")
    assert service.rebuild()["status"] == "error"

    assert service.rebuild("conversations")["detail"] == "Unknown rebuild target: conversations"


def test_clear_requires_confirmation_and_refuses_unsupported_scopes(tmp_path):
    store = _Store(
        memories=[
            {"id": "m1", "kind": "long_term", "content": "a"},
            {"id": "m2", "kind": "workspace", "content": "b"},
        ]
    )
    service = _service(tmp_path, store=store)

    with pytest.raises(ValueError, match="confirm=true"):
        service.clear(scope="long_term")

    assert service.clear(scope="long_term", confirm=True) == {
        "cleared": "long_term",
        "removed": ["m1"],
        "count": 1,
    }

    # "workspace" is both a tier name and a memory *kind*, and the kind branch
    # is checked first, so this clears only kind == "workspace" memories.
    assert service.clear(scope="workspace", confirm=True)["removed"] == ["m2"]

    with pytest.raises(ValueError, match="graph clear is disabled"):
        service.clear(scope="graph", confirm=True)

    with pytest.raises(ValueError, match="unsupported clear scope: vector"):
        service.clear(scope="vector", confirm=True)
