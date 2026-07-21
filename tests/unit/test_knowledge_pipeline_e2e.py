"""Knowledge Pipeline E2E deterministic harness (backlog #15, review §5.2 H3).

One flowing test over the *real* local stack — no model, no network, no
fakes on the knowledge path: temp folder → ``IngestionPipeline.ingest_folder``
→ ``hybrid_search`` (with the v9.9.3 ``query_class`` field) → chat context
building (``build_context_quality`` + ``ContextAssembler``) → automation
``suggestions`` grounded in the same graph. Assertions at every stage so a
regression pinpoints the broken link in the chain.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.context import ContextAssembler
from lattice_brain.graph.fusion import QUERY_CLASSES
from lattice_brain.graph.store import KnowledgeGraphStore
from lattice_brain.ingestion import IngestionPipeline
from latticeai.api.chat_helpers import build_context_quality
from latticeai.services.automation_intelligence import AutomationIntelligenceService

OWNER = "e2e@example.com"

_FILES = {
    "tea_sourcing_notes.md": (
        "# Jasmine tea sourcing notes\n\n"
        "Hana Trading supplies jasmine pearl tea from Fujian. Their spring "
        "harvest lots scored highest in the March cupping session.\n"
    ),
    "pricing_decision.txt": (
        "Decision: we set the jasmine tea import pricing at 12,000 won per "
        "kilogram for the autumn season. The margin target stays at 30 "
        "percent and shipping is renegotiated quarterly.\n"
    ),
    "brain_roadmap.md": (
        "# Brain roadmap\n\n"
        "Q3 focuses on retrieval fusion quality, folder watch mode, and the "
        "review inbox for automation drafts.\n"
    ),
}

_RECALL_QUERY = "jasmine tea import pricing decision"


class RecurringQuestionConversations:
    """Deterministic conversation history: the same question asked daily."""

    def __init__(self):
        self.items = [
            {"role": "user", "content": "오늘 tea 결정 사항 정리해줘",
             "timestamp": f"2026-07-{day:02d}T08:00:00"}
            for day in (17, 18, 19)
        ]

    def history(self, **kwargs):
        return list(self.items)


class EmptyWorkflowStore:
    def list_workflows(self, **kwargs):
        return {"workflows": []}


def test_knowledge_pipeline_end_to_end(tmp_path):
    # ── Stage 0: a temp folder with three small text/markdown files ──────
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    for name, content in _FILES.items():
        (corpus / name).write_text(content, encoding="utf-8")

    store = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    pipeline = IngestionPipeline(store, enable_graph=True)

    # ── Stage 1: folder ingestion through the one pipeline door ──────────
    summary = pipeline.ingest_folder(corpus, owner=OWNER, user_email=OWNER)
    assert summary["status"] == "ok", summary
    assert summary["scanned"] == len(_FILES)
    assert summary["matched"] == len(_FILES)
    assert summary["ingested"] == len(_FILES)
    assert summary["failed"] == 0

    documents = store.find_documents_by_uri_prefix(str(corpus))
    assert len(documents) == len(_FILES)
    node_by_title = {doc["title"]: doc["id"] for doc in documents}
    assert set(node_by_title) == set(_FILES)

    # Re-ingesting is idempotent: duplicates, not copies.
    again = pipeline.ingest_folder(corpus, owner=OWNER, user_email=OWNER)
    assert again["duplicate"] == len(_FILES)
    assert again["ingested"] == 0
    assert len(store.find_documents_by_uri_prefix(str(corpus))) == len(_FILES)

    # ── Stage 2: hybrid search finds the expected document ───────────────
    result = store.hybrid_search(_RECALL_QUERY, top_k=5)
    # v9.9.3 retrieval fusion: every hybrid response classifies its query.
    assert "query_class" in result
    assert result["query_class"] in QUERY_CLASSES
    matches = result["matches"]
    assert matches, "hybrid search returned no matches for the recall query"
    retrieved_ids = {m.get("node_id") or m.get("id") for m in matches}
    assert node_by_title["pricing_decision.txt"] in retrieved_ids

    # ── Stage 3: chat context building over the same graph ───────────────
    context_quality = build_context_quality(_RECALL_QUERY, knowledge_graph=store)
    assert context_quality["mode"] in {"hybrid", "lexical_only"}
    assert context_quality["nodes"] >= 1
    assert "limited" in context_quality

    assembler = ContextAssembler(
        hybrid_search=lambda query, **kwargs: store.hybrid_search(query, top_k=5),
    )
    assembled = assembler.assemble(_RECALL_QUERY, user_email=OWNER)
    knowledge_sections = [s for s in assembled.sections if s.source == "knowledge"]
    assert len(knowledge_sections) == 1
    section = knowledge_sections[0]
    assert "pricing_decision.txt" in section.content
    # Provenance: the context can answer "why is this in my prompt?".
    provenance_ids = {entry.get("id") for entry in section.provenance}
    assert node_by_title["pricing_decision.txt"] in provenance_ids
    trace = assembled.trace()
    assert trace["used_approx_tokens"] > 0

    # ── Stage 4: suggestions field, grounded in the same Brain ───────────
    automation = AutomationIntelligenceService(
        conversation_store=RecurringQuestionConversations(),
        knowledge_graph=store,
        store=EmptyWorkflowStore(),
        enable_graph=True,
    )
    report = automation.suggestions(user_email=OWNER)
    assert "suggestions" in report
    recurring = [s for s in report["suggestions"] if s["kind"] == "recurring_question"]
    assert recurring, "the repeated question did not become a suggestion"
    suggestion = recurring[0]
    assert suggestion["reason"]["count"] == 3
    # KG grounding ran against the real graph (not "grounding unavailable").
    assert suggestion["confidence_factors"]["kg_related_nodes"] is not None
    assert 0.0 < suggestion["confidence"] <= 1.0
