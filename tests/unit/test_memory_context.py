"""T5: Memory System (typed Decision/Experience records) + Context System.

Memory records flow through the unified pipeline (provenance, typed nodes);
simulation runs are refused at the memory boundary. The ContextAssembler
produces budgeted, provenance-carrying sections with honest absence.
"""

import pytest

from lattice_brain.context import ContextAssembler, approx_tokens
from lattice_brain.graph.store import KnowledgeGraphStore
from lattice_brain.ingestion import IngestionPipeline
from lattice_brain.memory import BrainMemory


def _brain(tmp_path):
    kg = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    return kg, IngestionPipeline(kg, hooks=None, enable_graph=True)


# ── Memory System ──────────────────────────────────────────────────────────

def test_decision_record_is_typed_node_with_provenance(tmp_path):
    kg, pipe = _brain(tmp_path)
    memory = BrainMemory(pipe)
    result = memory.record_decision(
        "Use SQLite for the brain store", "evaluated alternatives; local-first wins",
        user_email="a@b.c", conversation_id="c1",
    )
    assert result["status"] == "ok"
    node_id = result["node_id"]
    with kg._connect() as conn:
        row = conn.execute("SELECT type FROM nodes WHERE id=?", (node_id,)).fetchone()
        v2 = conn.execute("SELECT type FROM nodes_v2 WHERE id=?", (node_id,)).fetchone()
    assert row["type"] == "Decision"
    assert v2["type"] == "DECISION"
    prov = kg.get_provenance(node_id)
    assert prov is not None and prov["source_type"] == "decision"


def test_real_run_becomes_experience(tmp_path):
    kg, pipe = _brain(tmp_path)
    memory = BrainMemory(pipe)
    result = memory.record_experience(
        "Refactored the auth module", "completed with passing tests",
        run={"id": "run-1", "mode": "llm", "status": "ok", "agent_id": "agent:executor"},
        user_email="a@b.c",
    )
    assert result["status"] == "ok"
    prov = kg.get_provenance(result["node_id"])
    assert prov is not None and prov["source_type"] == "experience"


def test_simulation_run_is_refused_as_experience(tmp_path):
    _, pipe = _brain(tmp_path)
    memory = BrainMemory(pipe)
    result = memory.record_experience(
        "Fake run", "deterministic theater",
        run={"id": "run-2", "mode": "simulation", "status": "ok"},
    )
    assert result["status"] == "rejected"
    assert "simulation" in result["detail"]


def test_decision_requires_title(tmp_path):
    _, pipe = _brain(tmp_path)
    with pytest.raises(ValueError):
        BrainMemory(pipe).record_decision("   ")


# ── Context System ─────────────────────────────────────────────────────────

def _assembler(**overrides):
    seams = {
        "memory_recall": lambda q, **kw: {"results": [
            {"source": "workspace", "id": "m1", "kind": "preferences", "snippet": "prefers Korean replies", "score": 1.0},
            {"source": "graph", "id": "g1", "kind": "node", "snippet": "ignored here", "score": 0.5},
        ]},
        "hybrid_search": lambda q, **kw: {"matches": [
            {"id": "doc:1", "title": "Spec", "summary": "the project spec", "score": 0.9, "sources": ["keyword", "vector"]},
        ]},
        "notes_context": lambda q: "--- Document: runbook ---\nupgrade steps",
        "recent_chat": lambda **kw: "user: hello\nassistant: hi",
    }
    seams.update(overrides)
    return ContextAssembler(**seams)


def test_assembled_context_carries_provenance_per_section():
    ctx = _assembler().assemble("query", user_email="a@b.c", conversation_id="c1")
    trace = ctx.trace()
    by_source = {s["source"]: s for s in trace["sections"]}
    assert set(by_source) == {"memory", "knowledge", "notes", "recent_chat"}
    assert by_source["memory"]["provenance"][0]["id"] == "m1"
    assert by_source["knowledge"]["provenance"][0]["sources"] == ["keyword", "vector"]
    assert "prefers Korean replies" in ctx.text
    assert "[Knowledge]" in ctx.text


def test_context_retrieval_callbacks_receive_active_workspace():
    seen = {}

    def hybrid(q, **kwargs):
        seen["hybrid"] = kwargs
        return {"matches": []}

    def notes(q, **kwargs):
        seen["notes"] = kwargs
        return ""

    def recent(**kwargs):
        seen["recent"] = kwargs
        return ""

    _assembler(hybrid_search=hybrid, notes_context=notes, recent_chat=recent).assemble(
        "query",
        user_email="alice@example.com",
        workspace_id="org-1",
    )

    assert seen["hybrid"]["workspace_id"] == "org-1"
    assert seen["notes"]["workspace_id"] == "org-1"
    assert seen["hybrid"]["user_email"] == "alice@example.com"
    assert seen["notes"]["user_email"] == "alice@example.com"
    assert seen["recent"]["workspace_id"] == "org-1"


def test_context_callback_type_error_does_not_retry_without_workspace_scope():
    calls = []

    def scoped_failure(q, **kwargs):
        calls.append(kwargs)
        raise TypeError("backend adapter failed")

    context = _assembler(hybrid_search=scoped_failure).assemble(
        "query",
        user_email="alice@example.com",
        workspace_id="org-1",
    )

    assert calls == [{"limit": 5, "user_email": "alice@example.com", "workspace_id": "org-1"}]
    assert "knowledge" not in {section.source for section in context.sections}


def test_budget_trims_lowest_priority_first():
    ctx = _assembler(
        recent_chat=lambda **kw: "x" * 4000,  # ~1000 approx tokens
    ).assemble("query", budget=60)
    trace = ctx.trace()
    assert ctx.approx_tokens <= 60
    recent = next(s for s in trace["sections"] if s["source"] == "recent_chat")
    memory = next(s for s in trace["sections"] if s["source"] == "memory")
    assert recent["truncated"] is True
    assert memory["truncated"] is False, "memories are highest priority"


def test_absent_seams_contribute_nothing():
    ctx = ContextAssembler().assemble("query")
    assert ctx.sections == []
    assert ctx.text == ""


def test_failing_seam_is_isolated():
    def boom(q, **kw):
        raise RuntimeError("backend down")
    ctx = _assembler(hybrid_search=boom).assemble("query")
    sources = {s.source for s in ctx.sections}
    assert "memory" in sources and "recent_chat" in sources
    assert all("backend down" not in s.content for s in ctx.sections)


def test_approx_tokens_is_documented_chars_over_four():
    assert approx_tokens("abcd" * 10) == 10
    assert approx_tokens("") == 0


def test_agent_learnings_become_experience_records(tmp_path):
    """The agent memory-update path records Experiences through the brain
    port instead of dumping vault markdown with swallowed errors."""
    import asyncio

    from latticeai.core.agent import AgentRunContext

    kg, pipe = _brain(tmp_path)
    memory = BrainMemory(pipe)

    captured = {}

    class _Deps:
        memory_updater_prompt = "extract"
        brain_memory = memory

        @staticmethod
        async def generate(**kwargs):
            return '{"action": "memory", "save_to_knowledge": true, "learnings": ["always run tests"]}'

        @staticmethod
        def knowledge_save(*a, **kw):
            captured["vault_dump"] = True

    from latticeai.core.agent import SingleAgentRuntime

    runtime = SingleAgentRuntime.__new__(SingleAgentRuntime)
    runtime.deps = _Deps()
    ctx = AgentRunContext()
    ctx.transcript = [{"state": "DONE"}]

    class _Req:
        message = "refactor the auth module"

    asyncio.run(runtime.memory_update(ctx, _Req(), "a@b.c"))
    assert "vault_dump" not in captured, "brain port must take precedence over vault dump"
    exp = [m for m in kg.search("refactor")["matches"] if m.get("type") == "Experience"]
    assert exp, "the learning must land as a typed Experience node"
    prov = kg.get_provenance(exp[0]["id"])
    assert prov is not None and prov["source_type"] == "experience"
