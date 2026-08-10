"""wpb04 — never-taken branch directions in the service layer.

Every test here drives the *unexecuted* side of a decision: a loop that runs
zero times, an ``if`` that has only ever been true, an ``elif`` chain that has
never fallen through. Collaborators are injected fakes and every path lives
under ``tmp_path``, so nothing depends on the developer's ``~/.ltcai`` state.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

from latticeai.services import p_reinforce as p_reinforce_module
from latticeai.services.automation_intelligence import AutomationIntelligenceService
from latticeai.services.folder_watch import FolderWatchService
from latticeai.services.p_reinforce import PReinforceGardener
from latticeai.services.platform_runtime import PlatformRuntime
from latticeai.services.search_service import SearchService

# A question that repeats but matches none of the three intent rules, so the
# pattern keeps ``recipe_id = None`` all the way into the suggestion.
UNMATCHED_Q_LATE = "고양이 사료 어디에 두었지?"
UNMATCHED_Q_EARLY = "고양이 사료 어디에 두었지 알려줘?"


# ── automation_intelligence ──────────────────────────────────────────────────


class _Conversations:
    def __init__(self, items: List[Dict[str, Any]]) -> None:
        self.items = items

    def history(self, **_kwargs):
        return list(self.items)


def _msg(content: str, ts: str) -> Dict[str, Any]:
    return {"role": "user", "content": content, "timestamp": ts}


def _out_of_order_history() -> _Conversations:
    """The newest question first — the store is not required to sort."""
    return _Conversations([
        _msg(UNMATCHED_Q_LATE, "2026-07-20T09:00:00"),
        _msg(UNMATCHED_Q_EARLY, "2026-07-18T09:00:00"),
    ])


def test_an_older_repeat_never_rewrites_the_pattern_representative():
    """automation_intelligence.py:246→249 — the ``>= last_asked`` false arc."""
    service = AutomationIntelligenceService(
        conversation_store=_out_of_order_history(), knowledge_graph=None,
        store=None, enable_graph=False,
    )

    report = service.question_patterns()

    assert len(report["patterns"]) == 1
    pattern = report["patterns"][0]
    assert pattern["count"] == 2
    # The older phrasing is recorded as evidence but never becomes the label,
    # and it does not drag ``last_asked`` backwards.
    assert pattern["representative"] == UNMATCHED_Q_LATE
    assert pattern["last_asked"] == "2026-07-20T09:00:00"
    assert sorted(pattern["examples"]) == sorted([UNMATCHED_Q_EARLY, UNMATCHED_Q_LATE])


def test_a_pattern_matching_no_intent_rule_keeps_a_null_recipe():
    """automation_intelligence.py:254→253 — the intent loop that finds nothing,
    and 74→76 / 362→364 — the recipe-less confidence and dedup arcs."""
    service = AutomationIntelligenceService(
        conversation_store=_out_of_order_history(), knowledge_graph=None,
        store=None, enable_graph=False,
    )

    pattern = service.question_patterns()["patterns"][0]
    assert pattern["intent"] == "recurring_question", "no rule renamed the intent"
    assert pattern["recipe_id"] is None

    payload = service.suggestions()

    assert payload["quality"]["suppressed_duplicates"] == 0
    item = next(i for i in payload["suggestions"] if i["kind"] == "recurring_question")
    assert item["recipe_id"] is None
    assert item["confidence_factors"]["intent_match"] is False
    # No intent match → no +0.15 bonus, but still above the suppression gate.
    assert item["confidence"] == 0.53


# ── folder_watch ─────────────────────────────────────────────────────────────


class _FailingPipeline:
    """Every ingest fails, so each readable file appends one error."""

    def __init__(self) -> None:
        self.seen: List[str] = []

    def available(self) -> bool:
        return True

    def ingest(self, item, user_email=None):
        self.seen.append(item.metadata["relative_path"])
        return SimpleNamespace(status="failed", duplicate=False, detail="store offline")


def _watch_service(tmp_path: Path, pipeline: Any) -> FolderWatchService:
    return FolderWatchService(
        pipeline=pipeline,
        config_path=tmp_path / "state" / "folder_watch.json",
        interval_seconds=3600,  # scans are driven explicitly
    )


def test_disabling_by_path_walks_past_other_watches_and_keeps_the_poller(tmp_path):
    """folder_watch.py:155→154 (path mismatch) and 162→164 (another watch
    is still enabled, so the poller must not be stopped)."""
    first = tmp_path / "alpha"
    second = tmp_path / "beta"
    for root in (first, second):
        root.mkdir()
        (root / "note.txt").write_text("내용", encoding="utf-8")
    service = _watch_service(tmp_path, _FailingPipeline())
    kept = service.enable(first)["watch"]["id"]
    service.enable(second)

    removed = service.disable(path=str(second))

    assert removed["status"] == "ok"
    assert removed["watch"]["path"] == str(second.resolve())
    status = service.status()
    assert [w["id"] for w in status["watches"]] == [kept]
    assert status["enabled_count"] == 1


def test_scan_stops_recording_errors_after_the_twenty_sample_cap(tmp_path, monkeypatch):
    """folder_watch.py:256→258 and 279→240 — both error-append sites once the
    20-entry sample is full. The scan still counts every failure."""
    root = tmp_path / "corpus"
    root.mkdir()
    service = _watch_service(tmp_path, _FailingPipeline())
    watch_id = service.enable(root)["watch"]["id"]

    # 21 files whose ingest fails, plus one that cannot even be read. Sorted
    # order puts the unreadable file last, so the 20-error sample is already
    # full when both late branches run.
    for index in range(21):
        (root / ("f%02d.txt" % index)).write_text("본문", encoding="utf-8")
    unreadable = root / "zz_unreadable.txt"
    unreadable.write_text("본문", encoding="utf-8")

    original_read_text = Path.read_text

    def _read_text(self, *args, **kwargs):
        if self.name == unreadable.name:
            raise OSError("permission denied")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _read_text)

    result = service.scan_once(watch_id)

    assert result["status"] == "ok"
    assert result["new"] == 22
    assert result["failed"] == 22, "every failure is counted"
    assert len(result["errors"]) == 20, "only the first 20 are sampled"
    assert result["errors"][0]["path"] == "f00.txt"
    assert all("read failed" not in e["detail"] for e in result["errors"]), (
        "the unreadable file arrived after the sample filled up"
    )


# ── p_reinforce ──────────────────────────────────────────────────────────────


def _gardener(tmp_path: Path, monkeypatch, *, kg=None) -> PReinforceGardener:
    monkeypatch.setattr(p_reinforce_module, "BRAIN_DIR", tmp_path / "vault")
    return PReinforceGardener(ingestion_pipeline=None, knowledge_graph=kg)


class _FrozenDatetime(datetime):
    """A clock the log-file name cannot drift under (midnight rollover)."""

    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 7, 20, 9, 30, 0)


def test_the_daily_log_header_is_written_once_per_day(tmp_path, monkeypatch):
    """p_reinforce.py:196→198 — the second append sees a non-empty log."""
    gardener = _gardener(tmp_path, monkeypatch)
    monkeypatch.setattr(p_reinforce_module, "datetime", _FrozenDatetime)

    gardener._append_log("첫 메모", "00_Raw", "a.md")
    gardener._append_log("둘째 메모", "00_Raw", "b.md")

    logs = sorted((tmp_path / "vault" / "40_Log").glob("*.md"))
    assert [path.name for path in logs] == ["2026-07-20.md"]
    body = logs[0].read_text(encoding="utf-8")
    assert body.count("# 📅 Log") == 1, "the header is not repeated"
    assert "`00_Raw/a.md`" in body and "`00_Raw/b.md`" in body


def test_tree_reports_a_missing_garden_folder_as_empty(tmp_path, monkeypatch):
    """p_reinforce.py:208→220 — a structure folder that no longer exists."""
    gardener = _gardener(tmp_path, monkeypatch)
    (tmp_path / "vault" / "10_Wiki" / "note.md").write_text("지식", encoding="utf-8")
    shutil.rmtree(tmp_path / "vault" / "00_Raw")

    tree = gardener.get_tree()

    by_name = {folder["name"]: folder for folder in tree["folders"]}
    assert by_name["00_Raw"]["files"] == []
    assert by_name["00_Raw"]["count"] == 0
    assert by_name["10_Wiki"]["count"] == 1


class _ExplodingGraph:
    def search(self, *_args, **_kwargs):
        raise RuntimeError("graph offline")


def test_unscoped_context_falls_back_to_the_vault_when_the_brain_fails(tmp_path, monkeypatch):
    """p_reinforce.py:260→264 — no workspace scope, so a brain failure is
    allowed to fall back to the file scan; and 274→268 — a note that does not
    match the query keeps the scan going."""
    gardener = _gardener(tmp_path, monkeypatch, kg=_ExplodingGraph())
    vault = tmp_path / "vault"
    (vault / "00_Raw" / "unrelated.md").write_text("전혀 다른 이야기", encoding="utf-8")
    (vault / "10_Wiki" / "match.md").write_text("릴리스 절차 정리", encoding="utf-8")

    context = gardener.get_relevant_context("릴리스", limit=3)

    assert "--- Document: match.md ---" in context
    assert "unrelated.md" not in context


def test_scoped_context_refuses_to_fall_back_when_the_brain_fails(tmp_path, monkeypatch):
    """The other side of 260: with a scope, a brain failure returns nothing
    rather than reading files that were never workspace-scoped."""
    gardener = _gardener(tmp_path, monkeypatch, kg=_ExplodingGraph())
    (tmp_path / "vault" / "10_Wiki" / "match.md").write_text("릴리스 절차", encoding="utf-8")

    assert gardener.get_relevant_context("릴리스", allowed_workspaces={"w1"}) == ""


# ── search_service ───────────────────────────────────────────────────────────


class _Graph:
    """A graph store that answers every read from configured lists."""

    def __init__(self, *, matches=None, nodes=None, edges=None, relationships=None,
                 vector=None, index=None) -> None:
        self.matches = matches or []
        self.nodes = nodes or []
        self.edges = edges or []
        self.relationships = relationships or []
        self.vector = vector or []
        self.index = index or {}

    def filter_scoped_nodes(self, matches, allowed, *, include_legacy_global=False):
        return list(matches)

    def search(self, query, limit, *, allowed_workspaces=None, include_legacy_global=False):
        return {"query": query, "matches": list(self.matches)}

    def relationship_search(self, *, query="", node_id="", relationship_type="",
                            limit=30, allowed_workspaces=None, include_legacy_global=False):
        return {"relationships": list(self.relationships)}

    def traverse(self, node_id, *, depth=1, limit=100, allowed_workspaces=None,
                 include_legacy_global=False):
        return {"nodes": list(self.nodes), "edges": list(self.edges)}

    def get_node(self, node_id, *, allowed_workspaces=None, include_legacy_global=False):
        return {"id": node_id, "type": "Concept", "title": node_id}

    def vector_search(self, query, *, limit=30, min_score=0.0):
        return {"matches": list(self.vector), "embedding_model": "fake", "embedding_dim": 8}

    def index_status(self):
        return self.index


def _node(node_id: str, **extra) -> Dict[str, Any]:
    payload = {"id": node_id, "type": "Concept", "title": node_id, "summary": node_id}
    payload.update(extra)
    return payload


def test_neighbour_expansion_without_edges_records_no_relationship():
    """search_service.py:375→379 — the edge-lookup loop runs zero times."""
    graph = _Graph(matches=[_node("root")], nodes=[_node("root"), _node("orphan")], edges=[])
    service = SearchService(graph_store=graph)

    payload = service.graph_search("릴리스", expand_depth=1)

    by_id = {m["id"]: m for m in payload["matches"]}
    assert set(by_id) == {"root", "orphan"}
    context = by_id["orphan"]["graph_context"]
    assert [c["reason"] for c in context] == ["neighbor_expansion"]
    assert "relationship" not in context[0], "no edge connected the pair"


def test_a_channel_repeating_a_node_is_credited_once_in_sources():
    """search_service.py:477→479 — the same source contributing a key twice."""
    graph = _Graph(matches=[_node("dup"), _node("dup")])
    service = SearchService(graph_store=graph)

    payload = service.hybrid_search("릴리스", weights={"keyword": 1.0, "vector": 0.0, "graph": 0.0})

    fused = [m for m in payload["matches"] if m["id"] == "dup"]
    assert len(fused) == 1
    assert fused[0]["sources"].count("keyword") == 1, (
        "the duplicate row must not list its source twice"
    )


def test_node_detail_can_skip_the_neighbourhood():
    """search_service.py:581→591 — ``include_neighbors=False``."""
    graph = _Graph(nodes=[_node("n1")])
    service = SearchService(graph_store=graph)

    payload = service.node("n1", include_neighbors=False)

    assert payload["node"]["id"] == "n1"
    assert "neighborhood" not in payload


def test_embeddings_status_without_a_graph_reports_no_index():
    """search_service.py:666→686 — the index block is skipped entirely."""
    payload = SearchService(graph_store=None).embeddings_status()

    assert payload["index"] == {}
    assert payload["last_indexed_at"] is None
    assert payload["provider"] == "hash"
    assert payload["state"] == "fallback"


# ── platform_runtime ─────────────────────────────────────────────────────────


class _Store:
    def __init__(self, *, workflows=None) -> None:
        self.workflows = workflows or {}
        self.memories: List[Dict[str, Any]] = []
        self.workflow_runs: List[Dict[str, Any]] = []

    def search_memories(self, goal, *, user_email=None, workspace_id=None):
        return {"memories": list(self.memories)}

    def list_memories(self, *, user_email=None, workspace_id=None):
        return {"memories": list(self.memories)}

    def load_state(self):
        return {}

    def get_workflow(self, workflow_id, *, workspace_id=None):
        if workflow_id not in self.workflows:
            raise FileNotFoundError(workflow_id)
        return self.workflows[workflow_id]

    def record_workflow_run(self, **kwargs):
        self.workflow_runs.append(kwargs)
        return {"id": "wfrun-1"}


def _runtime(**overrides) -> PlatformRuntime:
    kwargs: Dict[str, Any] = dict(
        store=_Store(),
        workspace_service=None,
        plugin_registry=None,
        get_current_user=lambda _request: "member@example.com",
        workspace_graph=lambda: None,
        workspace_scope_from_request=lambda _request: "w1",
        get_tool_permission=lambda name, args=None: {"requires_approval": False},
        hooks=None,
    )
    kwargs.update(overrides)
    return PlatformRuntime(**kwargs)


def test_recall_rows_without_a_snippet_are_not_evidence():
    """platform_runtime.py:179→173 — a recall row with nothing to quote."""
    store = _Store()
    store.memories = [{"content": "legacy 메모"}]
    recalled = [
        {"source": "brain", "title": "빈 행", "snippet": "   "},
        {"source": "brain", "title": "빈 행 2", "content": ""},
    ]
    runtime = _runtime(
        store=store,
        memory_recall=lambda goal, **_kw: {"results": recalled},
    )

    context = runtime._context_provider("member@example.com", "w1")("릴리스 정리")

    assert all("빈 행" not in line for line in context), (
        "empty snippets contribute nothing, so the legacy store answers instead"
    )
    assert any("legacy 메모" in line for line in context)


def test_an_agent_node_without_a_draft_hint_keeps_its_configured_roles():
    """platform_runtime.py:266→268 — the default-roles fallback is skipped."""
    seen: Dict[str, Any] = {}

    runtime = _runtime()
    runtime.run_agent = lambda goal, user, scope, **kwargs: seen.update(
        goal=goal, user=user, scope=scope, **kwargs
    ) or {"status": "ok"}

    node = {"config": {"goal": "정리", "roles": ["executor"], "mode": "live"}}
    result = runtime._agent_node_runner("member@example.com", "w1")(node=node, context={})

    assert result == {"status": "ok"}
    assert seen["roles"] == ["executor"], "an explicit role list is never widened"


def test_running_a_workflow_without_the_agent_runner_refuses_agent_nodes():
    """platform_runtime.py:290→292 — ``with_agent=False`` registers no runner."""
    workflow = {
        "id": "wf-1",
        "name": "agent only",
        "nodes": [{"id": "n1", "type": "agent", "config": {"goal": "정리"}}],
        "edges": [],
    }
    store = _Store(workflows={"wf-1": workflow})
    runtime = _runtime(store=store)

    result = runtime.run_workflow_by_id("wf-1", "member@example.com", "w1", with_agent=False)

    assert result["workflow_run_id"] == "wfrun-1"
    assert result["status"] != "completed", "an agent node cannot run without its runner"
    recorded = store.workflow_runs[0]
    assert recorded["status"] == result["status"]


def test_running_a_missing_workflow_is_an_honest_error():
    runtime = _runtime()

    assert runtime.run_workflow_by_id("nope", "u@example.com", "w1", with_agent=False) == {
        "error": "workflow not found: nope"
    }
