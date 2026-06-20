#!/usr/bin/env python3
"""Deterministic Brain recall/KG quality gate for CI.

This is intentionally small and fixture-driven. It guards the product promise
that the first Brain loop can save context, recall it with source evidence, and
show model-independent proof without relying on a live model.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from lattice_brain.quality import RetrievalBenchmarkRunner
from latticeai.services.memory_service import MemoryService


class _EvalStore:
    def __init__(self) -> None:
        self.rows = [
            {
                "id": "decision:pricing",
                "kind": "decision",
                "content": "Use local-first memory as the product wedge for early access customers.",
                "workspace_id": "personal",
                "tags": ["strategy"],
            },
            {
                "id": "note:first-loop",
                "kind": "note",
                "content": "The first five minute loop is add source, ask question, see Brain proof.",
                "workspace_id": "personal",
                "tags": ["onboarding"],
            },
        ]

    def list_memories(self, user_email=None, kind=None, workspace_id=None):
        rows = [row for row in self.rows if kind is None or row["kind"] == kind]
        if workspace_id is not None:
            rows = [row for row in rows if (row.get("workspace_id") or "personal") == workspace_id]
        return {"memories": rows}

    def list_memory_snapshots(self, workspace_id=None, limit=50):
        return {"snapshots": [{"snapshot_id": "eval-snapshot"}]}

    def search_memories(self, q, user_email=None, limit=20, workspace_id=None):
        terms = {term for term in (q or "").lower().split() if len(term) > 2}
        rows = [
            row for row in self.rows
            if (workspace_id is None or (row.get("workspace_id") or "personal") == workspace_id)
            and terms.intersection(row["content"].lower().split())
        ]
        return {"memories": rows[:limit]}


class _EvalGraph:
    def stats(self):
        return {"nodes": {"Document": 1, "Decision": 1, "Topic": 1}, "edges": {"mentions": 2}}

    def index_status(self):
        return {"vector_counts": {"node": 3, "chunk": 2}}

    def search(self, q, limit=20):
        return {
            "matches": [
                {
                    "id": "doc:first-loop",
                    "type": "Document",
                    "title": "First Brain Loop",
                    "summary": "Users add a source, ask a question, and see proof with citations.",
                    "score": 0.94,
                    "metadata": {"source": "eval:first-loop.md", "workspace_id": "personal"},
                }
            ][:limit]
        }


def _fail(message: str) -> int:
    print(f"brain-quality-eval: FAIL: {message}", file=sys.stderr)
    return 1


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        service = MemoryService(
            store=_EvalStore(),
            data_dir=Path(tmp),
            knowledge_graph=_EvalGraph(),
            enable_graph=True,
        )
        proof = service.brain_proof(
            recall_query="first five minute Brain proof",
            user_email="eval@example.com",
            workspace_id="personal",
            active_model="eval-model-a",
            limit=4,
        )

    recall_items = proof.get("recall", {}).get("items", [])
    if proof.get("model_continuity", {}).get("proven") is not True:
        return _fail("model continuity proof must be true when durable evidence exists")
    if proof.get("claims", {}).get("keeps_context_across_models") is not True:
        return _fail("model-independent context claim must be backed by durable evidence")
    if not recall_items:
        return _fail("recall must return at least one memory or KG item")
    if not any(str(item.get("source") or "").lower() in {"workspace", "graph"} for item in recall_items):
        return _fail("recall items must expose a source for citation UI")
    if proof.get("proofs", {}).get("graph_concepts", 0) < 1:
        return _fail("KG concepts must be counted in Brain proof")
    if proof.get("proofs", {}).get("vector_items", 0) < 1:
        return _fail("vector items must be counted in Brain proof")

    benchmark = RetrievalBenchmarkRunner()
    retrieval_metrics = benchmark.run_fixture(
        "7.3.0-hybrid-recall-regression",
        [
            {
                "query": "first five minute Brain proof",
                "relevant": ["note:first-loop", "doc:first-loop"],
                "retrieved": [item.get("id") for item in recall_items],
            },
            {
                "query": "local-first memory product wedge",
                "relevant": ["decision:pricing"],
                "retrieved": ["decision:pricing", "note:first-loop"],
            },
        ],
        top_k=4,
    )
    if retrieval_metrics.get("recall@5", 0.0) < 0.75:
        return _fail(f"hybrid recall regression below threshold: {retrieval_metrics}")
    if retrieval_metrics.get("precision@5", 0.0) < 0.4:
        return _fail(f"hybrid precision regression below threshold: {retrieval_metrics}")

    print(f"brain-quality-eval: OK {retrieval_metrics}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
