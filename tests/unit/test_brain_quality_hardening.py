#!/usr/bin/env python3
"""
tests/unit/test_brain_quality_hardening.py
Verifies all quality layer features with real execution (no mocks of the layer itself)
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import unittest

from lattice_brain.quality import (
    EmbeddingFallbackLabeller,
    HybridFusion,
    LatticeBrainQuality,
)


class TestBrainQualityHardening(unittest.TestCase):

    def setUp(self):
        self.q = LatticeBrainQuality()

    def test_embedding_fallback_and_drift(self):
        lab = EmbeddingFallbackLabeller(drift_threshold=0.1)
        l1 = lab.label("vec1", [0.1, 0.2, 0.3])
        self.assertIn("emb_cluster_", l1.label)
        self.assertGreater(l1.confidence, 0.7)
        plan = lab.generate_reindex_plan()
        self.assertIsInstance(plan, list)

    def test_bm25_and_hybrid_fusion(self):
        hf = HybridFusion(alpha=0.5)
        cands = [
            {"id": "d1", "text": "lattice brain quality layer embedding", "vector_score": 0.9},
            {"id": "d2", "text": "memory candidate scoring dedupe", "vector_score": 0.6}
        ]
        fused = hf.fuse("quality embedding", cands)
        self.assertGreater(fused[0]["fused_score"], 0.5)
        self.assertIn("fused_score", fused[0])

    def test_reranker_interface(self):
        rer = self.q.reranker
        cands = [{"id": "r1", "fused_score": 0.8}]
        out = rer.rerank("test", cands, top_k=1)
        self.assertEqual(len(out), 1)
        self.assertIn("rerank_score", out[0])

    def test_memory_full_pipeline(self):
        mems = [
            {"id": "m1", "content": "User prefers dark mode", "score": 0.8},
            {"id": "m2", "content": "User does not like light mode", "score": 0.7}
        ]
        mgr = self.q.memory_mgr
        c = mgr.extract_candidates(mems)
        c = mgr.score_candidates(c, "dark mode preference")
        c = mgr.dedupe(c)
        c = mgr.detect_conflicts(c)
        c = mgr.apply_retention(c)
        self.assertGreaterEqual(len(c), 1)
        self.assertTrue(any("conflict" in str(x.conflicts) for x in c if x.conflicts))

    def test_memory_conflict_detector_pairs_opposite_preferences(self):
        mgr = self.q.memory_mgr
        cands = mgr.extract_candidates([
            {"id": "old", "content": "User prefers light workspace theme"},
            {"id": "new", "content": "User does not like light workspace theme"},
        ])
        flagged = mgr.detect_conflicts(cands)
        by_id = {item.id: item for item in flagged}
        self.assertIn("conflict:contradicts:new", by_id["old"].conflicts)
        self.assertIn("conflict:contradicts:old", by_id["new"].conflicts)

    def test_graph_edge_quality(self):
        edges = [
            {"id": "e1", "source": "a", "target": "b", "type": "rel", "confidence": 0.9, "evidence": ["doc1"]},
            {"id": "e2", "source": "a", "target": "b", "type": "rel", "confidence": 0.85, "evidence": []}
        ]
        gm = self.q.graph_mgr
        qs = [gm.validate_edge(e) for e in edges]
        self.assertGreater(qs[0].quality_score, 0.7)
        dups = gm.detect_duplicate_edges(edges)
        self.assertEqual(len(dups), 1)  # same source-target-type
        metrics = gm.compute_quality_metrics(edges)
        self.assertIn("avg_conf", metrics)

    def test_structured_context_assembly(self):
        items = [
            {"section": "Facts", "content": "Project started 2025", "known": True, "confidence": 0.95},
            {"section": "Decisions", "content": "Use hybrid retrieval", "inferred": True}
        ]
        asm = self.q.context_assembler
        ctx = asm.assemble(items)
        self.assertIn("Facts", ctx)
        self.assertIn("guardrails", ctx["Facts"][0])
        cleaned = asm.apply_guardrails(ctx)
        self.assertGreaterEqual(len(cleaned["Facts"]), 1)

    def test_retrieval_benchmark_runner(self):
        br = self.q.benchmark
        res = br.run_fixture(
            "quality_hardening_v1",
            [
                {"query": "q1", "relevant": ["a", "b"], "retrieved": ["a", "b", "c"]},
                {"query": "q2", "relevant": ["x"], "retrieved": ["x", "z"]},
            ],
            top_k=3,
        )
        self.assertEqual(res["fixture"], "quality_hardening_v1")
        self.assertGreater(res["recall@5"], 0.8)
        summary = br.summary()
        self.assertIn("total_runs", summary)

    def test_graded_relevance_ndcg_rewards_ordering(self):
        # Graded relevance ({id: grade}) must make nDCG sensitive to ranking:
        # putting the highest-grade doc first scores strictly above the reverse.
        br = self.q.benchmark
        graded = {"a": 3, "b": 2, "c": 1}
        best = br.run_fixture("graded_best", [{"query": "q", "relevant": graded, "retrieved": ["a", "b", "c"]}], top_k=3)
        worst = br.run_fixture("graded_worst", [{"query": "q", "relevant": graded, "retrieved": ["c", "b", "a"]}], top_k=3)
        self.assertEqual(best["ndcg@5"], 1.0)
        self.assertLess(worst["ndcg@5"], best["ndcg@5"])
        # Recall treats graded keys as the relevant set (binary membership).
        self.assertEqual(best["recall@5"], 1.0)
        # Binary list relevance must still work unchanged (back-compat).
        binary = br.run_fixture("binary", [{"query": "q", "relevant": ["a", "b"], "retrieved": ["a", "b"]}], top_k=3)
        self.assertEqual(binary["ndcg@5"], 1.0)

if __name__ == "__main__":
    unittest.main(verbosity=2)
