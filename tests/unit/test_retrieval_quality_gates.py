"""Runtime retrieval quality gates + evidence explainability.

MemoryService.recall must (1) explain every result with matched terms and a
confidence band, (2) drop zero-evidence rows only when real lexical evidence
exists, and (3) report what the gate did so callers can trust the counts.
Brain proof recall items must carry the same explainability fields for the
citation UI.
"""

from latticeai.services.memory_service import MemoryService


class _Store:
    def __init__(self, memories):
        self._memories = memories

    def search_memories(self, q, user_email=None, limit=20, workspace_id=None):
        return {"memories": list(self._memories)}

    def list_memories(self, user_email=None, kind=None, workspace_id=None):
        return {"memories": list(self._memories)}

    def list_memory_snapshots(self, workspace_id=None, limit=50):
        return {"snapshots": []}


class _Graph:
    def __init__(self, matches):
        self._matches = matches

    def search(self, q, limit):
        return {"query": q, "matches": list(self._matches)}

    def stats(self):
        return {"nodes": {"Document": len(self._matches)}, "edges": {}}

    def index_status(self):
        return {"vector_counts": {"node": len(self._matches)}}


def _service(tmp_path, memories=(), matches=()):
    return MemoryService(
        store=_Store(list(memories)),
        data_dir=tmp_path,
        knowledge_graph=_Graph(list(matches)),
        enable_graph=True,
    )


def test_recall_explains_matches_and_confidence(tmp_path):
    svc = _service(
        tmp_path,
        memories=[{"id": "m1", "kind": "note", "content": "alpha beta gamma", "tags": []}],
        matches=[{"id": "node:1", "title": "alpha doc", "summary": "about alpha", "type": "Document"}],
    )
    res = svc.recall("alpha beta")
    by_id = {r["id"]: r for r in res["results"]}
    assert by_id["m1"]["matched_terms"] == ["alpha", "beta"]
    assert by_id["m1"]["confidence"] == "high"
    assert by_id["node:1"]["matched_terms"] == ["alpha"]
    assert by_id["node:1"]["confidence"] == "medium"


def test_recall_gate_drops_zero_evidence_rows_when_evidence_exists(tmp_path):
    svc = _service(
        tmp_path,
        memories=[
            {"id": "hit", "kind": "note", "content": "alpha topic", "tags": []},
            {"id": "noise", "kind": "note", "content": "unrelated content", "tags": []},
        ],
    )
    res = svc.recall("alpha")
    ids = [r["id"] for r in res["results"]]
    assert "hit" in ids
    assert "noise" not in ids
    gate = res["quality_gate"]
    assert gate["gate"] == "lexical-evidence/v1"
    assert gate["candidates"] == 2
    assert gate["passed"] == 1
    assert gate["filtered"] == 1


def test_recall_gate_never_empties_results_without_evidence(tmp_path):
    # Tokenization mismatch: nothing scores, so the tiers' own filtering wins
    # and the gate must not silently produce an empty recall.
    svc = _service(
        tmp_path,
        memories=[{"id": "m1", "kind": "note", "content": "완전히 다른 내용", "tags": []}],
    )
    res = svc.recall("찾을수없는질의어")
    assert [r["id"] for r in res["results"]] == ["m1"]
    assert res["quality_gate"]["filtered"] == 0
    assert res["results"][0]["confidence"] == "low"


def test_brain_proof_recall_items_carry_explainability(tmp_path):
    svc = _service(
        tmp_path,
        memories=[{"id": "m1", "kind": "note", "content": "alpha beta", "tags": [], "workspace_id": "personal"}],
        matches=[{"id": "node:1", "title": "alpha doc", "summary": "about alpha", "type": "Document"}],
    )
    proof = svc.brain_proof(recall_query="alpha beta", workspace_id="personal", limit=4)
    items = proof["recall"]["items"]
    assert items, "recall items must exist for the citation UI"
    for item in items:
        assert "matched_terms" in item
        assert item["confidence"] in {"high", "medium", "low"}
    top = items[0]
    assert top["matched_terms"], "top recall item must explain why it matched"
