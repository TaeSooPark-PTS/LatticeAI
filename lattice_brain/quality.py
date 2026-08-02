#!/usr/bin/env python3
"""
lattice_brain/quality.py
Pure Python Quality Layer for Lattice Brain (v6.4+ hardening)
- Does not modify any DB schema or existing APIs
- Self-contained, dependency-free core (stdlib only)
"""

from __future__ import annotations

import hashlib
import math
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# -----------------------------
# 1. Embedding Fallback Labelling + Drift/Reindex Plan
# -----------------------------
@dataclass
class EmbeddingLabel:
    vector_id: str
    label: str
    confidence: float
    drift_score: float = 0.0
    needs_reindex: bool = False

class EmbeddingFallbackLabeller:
    """Fallback labelling when primary embedding unavailable + drift detection"""

    def __init__(self, drift_threshold: float = 0.15):
        self.drift_threshold = drift_threshold
        self.label_cache: Dict[str, EmbeddingLabel] = {}
        self.vector_cache: Dict[str, List[float]] = {}

    def label(self, vector_id: str, embedding: Optional[List[float]] = None,
              metadata: Optional[Dict] = None) -> EmbeddingLabel:
        if embedding is None:
            label = "unembedded_fallback"
            conf = 0.3
        else:
            # Simple hash-based pseudo-label for determinism
            # Not a security hash: a short, stable label for grouping
            # vectors in reports. usedforsecurity=False states that and
            # keeps it working on FIPS builds where md5 is restricted.
            h = hashlib.md5(
                str(embedding[:4]).encode(), usedforsecurity=False
            ).hexdigest()[:8]
            label = f"emb_cluster_{h}"
            conf = 0.85

        drift = self._compute_drift(vector_id, embedding)
        needs_reindex = drift > self.drift_threshold

        el = EmbeddingLabel(vector_id, label, conf, drift, needs_reindex)
        self.label_cache[vector_id] = el
        if embedding is not None:
            self.vector_cache[vector_id] = [float(value) for value in embedding]
        return el

    def _compute_drift(self, vector_id: str, new_emb: Optional[List[float]]) -> float:
        old = self.vector_cache.get(vector_id)
        if old is None or new_emb is None:
            return 0.0
        return self._cosine_distance(old, new_emb)

    def _cosine_distance(self, a: List[float], b: List[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 1.0
        dot = sum(x * y for x, y in zip(a, b, strict=True))
        na = math.sqrt(sum(x*x for x in a))
        nb = math.sqrt(sum(x*x for x in b))
        return 1.0 - (dot / (na*nb + 1e-9))

    def generate_reindex_plan(self) -> List[str]:
        return [vid for vid, lab in self.label_cache.items() if lab.needs_reindex]

# -----------------------------
# 2. BM25 Lexical Scoring + Hybrid Fusion + Reranker Interface
# -----------------------------
class BM25Scorer:
    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.doc_freqs: Dict[str, int] = defaultdict(int)
        self.doc_lens: Dict[str, int] = {}
        self.corpus_size = 0
        self.avgdl = 0.0

    def fit(self, corpus: Dict[str, str]):
        self.corpus_size = len(corpus)
        total_len = 0
        for did, text in corpus.items():
            tokens = self._tokenize(text)
            self.doc_lens[did] = len(tokens)
            total_len += len(tokens)
            for t in set(tokens):
                self.doc_freqs[t] += 1
        self.avgdl = total_len / max(1, self.corpus_size)

    def score(self, query: str, doc_id: str, doc_text: str) -> float:
        tokens = self._tokenize(query)
        doc_tokens = self._tokenize(doc_text)
        score = 0.0
        for t in tokens:
            if t not in doc_tokens:
                continue
            tf = doc_tokens.count(t)
            df = self.doc_freqs.get(t, 0)
            idf = math.log((self.corpus_size - df + 0.5) / (df + 0.5) + 1)
            denom = tf + self.k1 * (1 - self.b + self.b * self.doc_lens.get(doc_id, 0) / self.avgdl)
            score += idf * tf * (self.k1 + 1) / (denom + 1e-9)
        return score

    def _tokenize(self, text: str) -> List[str]:
        return re.findall(r'\w+', text.lower())

class HybridFusion:
    """Hybrid fusion of lexical (BM25) + vector scores"""
    def __init__(self, alpha: float = 0.6):
        self.alpha = alpha  # weight for vector score
        self.bm25 = BM25Scorer()

    def fuse(self, query: str, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        # candidates: [{"id": , "text": , "vector_score": }]
        corpus = {c["id"]: c.get("text", "") for c in candidates}
        self.bm25.fit(corpus)
        results = []
        for c in candidates:
            lex = self.bm25.score(query, c["id"], c.get("text", ""))
            vec = c.get("vector_score", 0.5)
            fused = self.alpha * vec + (1 - self.alpha) * (lex / 10.0)  # normalize rough
            c["fused_score"] = round(fused, 4)
            results.append(c)
        return sorted(results, key=lambda x: x["fused_score"], reverse=True)

class RerankerInterface:
    """Pluggable reranker interface.

    Default is identity (fused score). When
    ``LATTICEAI_CROSS_ENCODER_RERANK=1`` and a CrossEncoder is importable,
    delegates to :func:`lattice_brain.graph.rerank.rerank_matches`. Failures
    never raise — always returns a ranked list.
    """

    def rerank(self, query: str, candidates: List[Dict], top_k: int = 5) -> List[Dict]:
        try:
            from lattice_brain.graph.rerank import rerank_matches

            # Map fused_score → score so the shared helper ranks consistently.
            prepared = []
            for c in candidates:
                item = dict(c)
                if "score" not in item:
                    item["score"] = item.get("fused_score", 0.0)
                prepared.append(item)
            result = rerank_matches(query, prepared, top_k=top_k)
            return list(result.get("matches") or [])
        except Exception:  # noqa: BLE001 — quality path must never raise
            for c in candidates:
                c["rerank_score"] = c.get("fused_score", 0.0)
            return sorted(
                candidates, key=lambda x: x.get("rerank_score", 0), reverse=True
            )[:top_k]

# -----------------------------
# 3. Memory Candidate Extraction / Scoring / Dedupe / Merge / Conflict / Retention
# -----------------------------

# Public content-signature helpers (v9.6.x graph-layer proactive seam).
# Extracted from MemoryQualityManager so the graph layer
# (lattice_brain.graph.proactive) and future ingestion gating can reuse the
# exact same dedupe semantics without instantiating the manager. Behaviour is
# byte-for-byte identical to the pre-existing private logic.

_SIGNATURE_STOPWORDS = {
    "a", "an", "and", "for", "i", "is", "it", "mode", "the", "to",
    "user", "users", "does", "do", "not", "like", "likes", "prefer",
    "prefers", "want", "wants",
}


def dedupe_key(content: str) -> str:
    """Stable near-exact signature for a piece of content.

    sha256 prefix over the normalized text head plus a coarse length bucket —
    the same key ``MemoryQualityManager.dedupe`` uses to collapse duplicates.
    Two texts with the same key are treated as exact/near-exact duplicates.
    """
    text = str(content or "")
    norm = " ".join(text.lower().split())[:200]
    return hashlib.sha256((norm + f"|{len(text)//50}").encode()).hexdigest()[:16]


def content_signature(content: str) -> set:
    """Token-set signature (stopword-filtered) for overlap/jaccard comparison.

    Mirrors ``MemoryQualityManager._content_signature`` — kept public so
    graph-layer duplicate/contradiction detection shares one definition.
    """
    tokens = set(re.findall(r"\w+", str(content or "").lower()))
    return {
        token
        for token in tokens
        if len(token) > 2 and token not in _SIGNATURE_STOPWORDS
    }


@dataclass
class MemoryCandidate:
    id: str
    content: str
    score: float = 0.0
    source: str = "unknown"
    timestamp: float = field(default_factory=time.time)
    conflicts: List[str] = field(default_factory=list)

class MemoryQualityManager:
    _NEGATION_PATTERNS = (
        "not",
        "does not",
        "don't",
        "do not",
        "싫어",
        "원하지 않",
        "하지 않",
        "반대",
    )
    _POSITIVE_PATTERNS = (
        "prefers",
        "prefer",
        "likes",
        "like",
        "wants",
        "want",
        "좋아",
        "선호",
        "원해",
    )

    def extract_candidates(self, memories: List[Dict]) -> List[MemoryCandidate]:
        return [MemoryCandidate(m["id"], m["content"], m.get("score", 0.6), m.get("source", "mem")) for m in memories]

    def score_candidates(self, cands: List[MemoryCandidate], query: str) -> List[MemoryCandidate]:
        for c in cands:
            # simple lexical overlap score
            overlap = sum(1 for w in query.lower().split() if w in c.content.lower())
            c.score = min(1.0, 0.4 + overlap * 0.15)
        return sorted(cands, key=lambda x: x.score, reverse=True)

    def dedupe(self, cands: List[MemoryCandidate], threshold: float = 0.85) -> List[MemoryCandidate]:
        """Advanced content dedup (KG scale slice).

        Uses sha256 prefix + normalized length bucket for better collision resistance
        than plain md5. Future: plug embedding cosine or simhash here.
        """
        kept = []
        seen = set()
        for c in cands:
            h = dedupe_key(c.content)
            if h not in seen:
                seen.add(h)
                kept.append(c)
        return kept

    def merge(self, cands: List[MemoryCandidate]) -> List[MemoryCandidate]:
        # naive merge by content prefix
        merged: Dict[str, MemoryCandidate] = {}
        for c in cands:
            key = c.content[:30]
            if key not in merged or c.score > merged[key].score:
                merged[key] = c
        return list(merged.values())

    def detect_conflicts(self, cands: List[MemoryCandidate]) -> List[MemoryCandidate]:
        """Flag local contradiction signals before proactive synthesis.

        The heuristic stays deterministic/offline: direct negation marks a row
        as risky, and pairwise token overlap catches common preference conflicts
        such as "prefers light mode" vs "does not like light mode".
        """
        for c in cands:
            lowered = c.content.lower()
            if any(pattern in lowered for pattern in self._NEGATION_PATTERNS):
                c.conflicts.append("conflict:possible_negation")

        signatures = [(c, self._content_signature(c.content)) for c in cands]
        for left_index, (left, left_sig) in enumerate(signatures):
            for right, right_sig in signatures[left_index + 1:]:
                if not left_sig or len(left_sig & right_sig) < 2:
                    continue
                left_negative = self._is_negative(left.content)
                right_negative = self._is_negative(right.content)
                left_positive = self._is_positive(left.content)
                right_positive = self._is_positive(right.content)
                if left_negative == right_negative:
                    continue
                if not (left_positive or right_positive):
                    continue
                left.conflicts.append(f"conflict:contradicts:{right.id}")
                right.conflicts.append(f"conflict:contradicts:{left.id}")
        return cands

    def _is_negative(self, content: str) -> bool:
        lowered = content.lower()
        return any(pattern in lowered for pattern in self._NEGATION_PATTERNS)

    def _is_positive(self, content: str) -> bool:
        lowered = content.lower()
        return any(pattern in lowered for pattern in self._POSITIVE_PATTERNS)

    def _content_signature(self, content: str) -> set[str]:
        return content_signature(content)

    # --- Large candidate #4 slice: proactive / temporal contradiction detection ---
    def detect_temporal_contradictions(self, memories: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Simple temporal + contradiction detector over memory list (proactive synthesis extension).

        Looks at negation keywords across similar contents + differing timestamps.
        Real version would walk graph historical projections + LLM judge.
        Returns flagged items with 'contradiction' tag.
        """
        flagged = []
        neg_tokens = ("not ", "반대", "거짓", "no longer", "never", "중단", "사용하지")
        pos_group = []
        neg_group = []
        for m in memories:
            c = (m.get("content") or "").lower()
            if any(t in c for t in neg_tokens):
                neg_group.append(m)
            else:
                pos_group.append(m)
        # cross check: if both pos and neg exist with time spread
        all_t = [m.get("timestamp") or m.get("created_at") or 0 for m in memories]
        if neg_group and pos_group and len(set(all_t)) > 1:
            for g in (pos_group + neg_group):
                gg = dict(g)
                gg["proactive_flag"] = "contradiction:temporal_negation"
                flagged.append(gg)
        return flagged

    def apply_retention(self, cands: List[MemoryCandidate], max_age_days: int = 90) -> List[MemoryCandidate]:
        now = time.time()
        return [c for c in cands if (now - c.timestamp) < max_age_days * 86400]

# -----------------------------
# 4. Graph Edge Validation / Confidence / Evidence / Duplicate Merge / Quality Metrics
# -----------------------------
@dataclass
class GraphEdgeQuality:
    edge_id: str
    confidence: float
    evidence_count: int
    is_duplicate: bool = False
    quality_score: float = 0.0

class GraphEdgeQualityManager:
    def validate_edge(self, edge: Dict[str, Any]) -> GraphEdgeQuality:
        conf = edge.get("confidence", 0.7)
        ev = len(edge.get("evidence", []))
        q = min(1.0, conf * 0.75 + min(ev, 5) / 5 * 0.3)
        return GraphEdgeQuality(edge.get("id", "e0"), conf, ev, quality_score=q)

    def detect_duplicate_edges(self, edges: List[Dict]) -> List[str]:
        seen: Dict[Any, Any] = {}
        dups: List[str] = []
        for e in edges:
            key = (e.get("source"), e.get("target"), e.get("type"))
            if key in seen:
                dups.append(str(e.get("id") or ""))
            else:
                seen[key] = e.get("id")
        return dups

    def merge_duplicate_edges(self, edges: List[Dict]) -> List[Dict]:
        # keep highest confidence
        best: Dict[Any, Dict[str, Any]] = {}
        for e in edges:
            key = (e.get("source"), e.get("target"), e.get("type"))
            if key not in best or e.get("confidence", 0) > best[key].get("confidence", 0):
                best[key] = e
        return list(best.values())

    def compute_quality_metrics(self, edges: List[Dict]) -> Dict[str, float]:
        if not edges:
            return {"avg_conf": 0.0, "avg_evidence": 0.0, "dup_rate": 0.0}
        confs = [e.get("confidence", 0.5) for e in edges]
        evs = [len(e.get("evidence", [])) for e in edges]
        dups = len(self.detect_duplicate_edges(edges))
        return {
            "avg_conf": round(sum(confs)/len(confs), 3),
            "avg_evidence": round(sum(evs)/len(evs), 2),
            "dup_rate": round(dups / len(edges), 3)
        }

# -----------------------------
# 5. Structured Context Assembly + Guardrails
# -----------------------------
@dataclass
class ContextGuardrails:
    known: bool = True
    inferred: bool = False
    stale: bool = False
    unknown: bool = False
    confidence: float = 0.8
    timestamp: float = field(default_factory=time.time)
    attribution: str = "system"

class StructuredContextAssembler:
    SECTIONS = ["Facts", "Decisions", "Preferences", "Relationships", "Projects", "Recent Events"]

    def assemble(self, items: List[Dict[str, Any]]) -> Dict[str, List[Dict]]:
        ctx: Dict[str, List[Dict]] = {s: [] for s in self.SECTIONS}
        for item in items:
            section = item.get("section", "Facts")
            if section not in ctx:
                section = "Facts"
            guard = ContextGuardrails(
                known=item.get("known", True),
                inferred=item.get("inferred", False),
                stale=item.get("stale", False),
                unknown=item.get("unknown", False),
                confidence=item.get("confidence", 0.75),
                attribution=item.get("attribution", "user")
            )
            item["guardrails"] = guard.__dict__
            ctx[section].append(item)
        return ctx

    def apply_guardrails(self, ctx: Dict[str, List[Dict]]) -> Dict[str, List[Dict]]:
        cleaned = {}
        for sec, items in ctx.items():
            cleaned[sec] = [i for i in items if not i.get("guardrails", {}).get("unknown", False)]
        return cleaned

# -----------------------------
# 6. Retrieval Benchmark Fixture Runner
# -----------------------------
def _relevance_grades(relevant: Any) -> Dict[str, float]:
    """Normalize a ``relevant`` spec into a ``{doc_id: grade}`` map.

    Accepts a graded dict (``{"doc": 3}``) or a binary list/set/tuple (grade 1
    for every id), so judged fixtures can express graded relevance for nDCG
    while older binary fixtures keep working unchanged.
    """
    if isinstance(relevant, dict):
        return {str(k): float(v) for k, v in relevant.items()}
    return {str(doc_id): 1.0 for doc_id in (relevant or [])}


class RetrievalBenchmarkRunner:
    def __init__(self):
        self.results: List[Dict] = []

    def run_fixture(self, fixture_name: str, queries: List[Any], top_k: int = 5) -> Dict[str, Any]:
        start = time.time()
        judged = [q for q in queries if isinstance(q, dict)]
        if judged:
            recalls = []
            precisions = []
            ndcgs = []
            for query in judged:
                grades = _relevance_grades(query.get("relevant"))
                relevant = set(grades)
                retrieved = list(query.get("retrieved") or [])[:top_k]
                if not relevant:
                    continue
                hits = [doc_id for doc_id in retrieved if doc_id in relevant]
                recalls.append(len(hits) / len(relevant))
                precisions.append(len(hits) / max(1, len(retrieved)))
                # Graded DCG: each retrieved doc contributes its relevance grade
                # discounted by log2(rank + 2). IDCG ranks the highest grades
                # first, so nDCG rewards putting the *most* relevant docs on top.
                # Binary fixtures fall back to grade 1 via _relevance_grades.
                dcg = sum(
                    grades.get(doc_id, 0.0) / math.log2(rank + 2)
                    for rank, doc_id in enumerate(retrieved)
                )
                ideal_grades = sorted(grades.values(), reverse=True)[:top_k]
                ideal = sum(g / math.log2(rank + 2) for rank, g in enumerate(ideal_grades))
                ndcgs.append(dcg / ideal if ideal else 0.0)
            recall = sum(recalls) / len(recalls) if recalls else 0.0
            precision = sum(precisions) / len(precisions) if precisions else 0.0
            ndcg = sum(ndcgs) / len(ndcgs) if ndcgs else 0.0
        else:
            recall = precision = ndcg = 0.0
        must_include = [
            bool(set(query.get("must_include") or []).intersection(set(list(query.get("retrieved") or [])[:top_k])))
            for query in judged
            if query.get("must_include")
        ]
        must_include_hit_rate = sum(1 for hit in must_include if hit) / len(must_include) if must_include else 1.0
        metrics = {
            "fixture": fixture_name,
            "queries": len(queries),
            "avg_latency_ms": round((time.time() - start) * 1000 / max(1, len(queries)), 2),
            "recall@5": round(recall, 4),
            "precision@5": round(precision, 4),
            "ndcg@5": round(ndcg, 4),
            f"recall@{top_k}": round(recall, 4),
            f"precision@{top_k}": round(precision, 4),
            f"ndcg@{top_k}": round(ndcg, 4),
            "must_include_hit_rate": round(must_include_hit_rate, 4),
            "top_k": top_k,
            "judged": len(judged),
        }
        self.results.append(metrics)
        return metrics

    def summary(self) -> Dict[str, Any]:
        if not self.results:
            return {"status": "no runs"}
        return {
            "total_runs": len(self.results),
            "avg_recall": round(sum(r["recall@5"] for r in self.results) / len(self.results), 3)
        }

# -----------------------------
# Main Quality Layer Facade
# -----------------------------
class LatticeBrainQuality:
    """Main entry point - pure Python quality hardening layer"""
    def __init__(self):
        self.embed_labeller = EmbeddingFallbackLabeller()
        self.hybrid = HybridFusion()
        self.reranker = RerankerInterface()
        self.memory_mgr = MemoryQualityManager()
        self.graph_mgr = GraphEdgeQualityManager()
        self.context_assembler = StructuredContextAssembler()
        self.benchmark = RetrievalBenchmarkRunner()

    def full_quality_pass(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """End-to-end quality pipeline (non-destructive)"""
        result = {"status": "ok", "timestamp": time.time()}

        # embedding
        if "embeddings" in payload:
            labels = [self.embed_labeller.label(e["id"], e.get("vector")) for e in payload["embeddings"]]
            result["embedding_labels"] = [label.__dict__ for label in labels]
            result["reindex_plan"] = self.embed_labeller.generate_reindex_plan()

        # hybrid retrieval
        if "retrieval" in payload:
            fused = self.hybrid.fuse(payload["retrieval"]["query"], payload["retrieval"]["candidates"])
            reranked = self.reranker.rerank(payload["retrieval"]["query"], fused)
            result["retrieval"] = {"fused": fused, "reranked": reranked}

        # memory
        if "memories" in payload:
            cands = self.memory_mgr.extract_candidates(payload["memories"])
            cands = self.memory_mgr.score_candidates(cands, payload.get("query", ""))
            cands = self.memory_mgr.dedupe(cands)
            cands = self.memory_mgr.merge(cands)
            cands = self.memory_mgr.detect_conflicts(cands)
            cands = self.memory_mgr.apply_retention(cands)
            result["memory_candidates"] = [c.__dict__ for c in cands]

        # graph
        if "graph_edges" in payload:
            eqs = [self.graph_mgr.validate_edge(e) for e in payload["graph_edges"]]
            result["graph_quality"] = [q.__dict__ for q in eqs]
            result["graph_metrics"] = self.graph_mgr.compute_quality_metrics(payload["graph_edges"])

        # context
        if "context_items" in payload:
            ctx = self.context_assembler.assemble(payload["context_items"])
            ctx = self.context_assembler.apply_guardrails(ctx)
            result["structured_context"] = ctx

        return result


__all__ = [
    "BM25Scorer",
    "ContextGuardrails",
    "content_signature",
    "dedupe_key",
    "EmbeddingFallbackLabeller",
    "EmbeddingLabel",
    "GraphEdgeQuality",
    "GraphEdgeQualityManager",
    "HybridFusion",
    "LatticeBrainQuality",
    "MemoryCandidate",
    "MemoryQualityManager",
    "RetrievalBenchmarkRunner",
    "RerankerInterface",
    "StructuredContextAssembler",
]
