"""Cross-encoder rerank option (v9.9.5) — identity default, opt-in CE path."""

from __future__ import annotations

import lattice_brain.graph.rerank as rerank_mod
from lattice_brain.graph.rerank import identity_rerank, rerank_matches


def test_identity_rerank_preserves_order_and_stamps_scores():
    candidates = [
        {"node_id": "a", "score": 0.9, "title": "A"},
        {"node_id": "b", "score": 0.5, "title": "B"},
    ]
    result = identity_rerank("q", candidates, top_k=1)
    assert result["mode"] == "identity"
    assert result["model"] is None
    assert len(result["matches"]) == 1
    assert result["matches"][0]["node_id"] == "a"
    assert result["matches"][0]["rerank_score"] == 0.9


def test_rerank_matches_defaults_to_identity_when_env_off(monkeypatch):
    monkeypatch.delenv("LATTICEAI_CROSS_ENCODER_RERANK", raising=False)
    candidates = [{"node_id": "x", "score": 0.4, "title": "x"}]
    result = rerank_matches("hello", candidates)
    assert result["mode"] == "identity"
    assert result["matches"][0]["node_id"] == "x"


def test_rerank_matches_force_cross_encoder_falls_back_without_model(monkeypatch):
    # Force the CE path but ensure the model load fails → identity + detail.
    monkeypatch.setenv("LATTICEAI_CROSS_ENCODER_RERANK", "1")
    monkeypatch.setattr(
        rerank_mod, "_load_cross_encoder",
        lambda model_id: (_ for _ in ()).throw(ImportError("no st")),
    )
    candidates = [
        {"node_id": "hi", "score": 0.8, "title": "hello world"},
        {"node_id": "lo", "score": 0.2, "title": "unrelated"},
    ]
    result = rerank_matches("hello", candidates, force=True)
    assert result["mode"] == "identity"
    assert "cross_encoder_unavailable" in (result.get("detail") or "")
    assert [m["node_id"] for m in result["matches"]] == ["hi", "lo"]


def test_rerank_matches_with_stub_cross_encoder_reorders(monkeypatch):
    class StubCE:
        def predict(self, pairs):
            # Prefer the candidate whose text contains "target".
            scores = []
            for _q, text in pairs:
                scores.append(1.0 if "target" in text else 0.1)
            return scores

    monkeypatch.setenv("LATTICEAI_CROSS_ENCODER_RERANK", "1")
    monkeypatch.setattr(rerank_mod, "_load_cross_encoder", lambda model_id: StubCE())
    candidates = [
        {"node_id": "noise", "score": 0.99, "title": "noise"},
        {"node_id": "hit", "score": 0.1, "title": "the target doc"},
    ]
    result = rerank_matches("find target", candidates, force=True, top_k=2)
    assert result["mode"] == "cross_encoder"
    assert result["matches"][0]["node_id"] == "hit"
    assert result["matches"][0]["rank"] == 1
    assert result["matches"][0]["scores"]["rerank"] == 1.0
