"""Unified retrieval across the memory tiers, and single-tier inspection.

:meth:`recall` blends three sources into one honestly comparable ranking:

* **workspace** memories, scored lexically as the fraction of query tokens
  present — the same scorer both text tiers use, so cross-tier ordering is real
  rather than an artifact of per-tier constants;
* **graph** nodes from the Knowledge Graph search;
* **vector** neighbours (v9.3.0 hybrid recall), which is what lets recall find
  knowledge phrased differently from the query. The vector tier is optional:
  any failure degrades recall to pure lexical instead of breaking it.

A quality gate then drops zero-score rows *only* when at least one row carries
real evidence, so the gate can never empty a recall. Every row reports its
confidence and which evidence kinds produced it.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ._contract import MemoryCore as _Core
from .constants import LOGGER, TIERS, WORKSPACE_KINDS, _visual_fields


class MemoryRecallMixin(_Core):
    """Recall, tiers, and per-tier inspection. Mixed into ``MemoryService``."""

    def tiers(self) -> Dict[str, Any]:
        return {"tiers": list(TIERS), "workspace_kinds": list(WORKSPACE_KINDS)}

    # ── recall (unified retrieval over the memory tiers) ───────────────────
    def recall(
        self,
        query: str,
        *,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
        limit: int = 20,
    ) -> Dict[str, Any]:
        q = str(query or "").strip()
        query_tokens = [tok for tok in q.lower().split() if tok]

        def _matched_terms(*texts: Any) -> List[str]:
            haystack = " ".join(str(t or "") for t in texts).lower()
            return [tok for tok in query_tokens if tok in haystack]

        def _lexical_score(matched: List[str]) -> float:
            # Honest, comparable relevance: fraction of query tokens present.
            # Both tiers share this scorer so the cross-tier ranking is real,
            # not an artifact of per-tier constants.
            if not query_tokens:
                return 0.0
            return round(len(matched) / len(query_tokens), 4)

        results: List[Dict[str, Any]] = []

        errors: List[Dict[str, str]] = []
        try:
            mem = self._store.search_memories(q, user_email=user_email, limit=limit, workspace_id=workspace_id).get("memories", [])
        except Exception as exc:
            LOGGER.exception("workspace memory search failed")
            errors.append({"source": "workspace", "detail": str(exc)})
            mem = []
        for m in mem:
            matched = _matched_terms(m.get("content"), " ".join(m.get("tags") or []), m.get("kind"))
            results.append({
                "source": "workspace",
                "id": m.get("id"),
                "title": (m.get("kind") or "memory"),
                "snippet": str(m.get("content") or "")[:240],
                "kind": m.get("kind"),
                "score": _lexical_score(matched),
                "matched_terms": matched,
                "tags": m.get("tags") or [],
            })

        if self._enable_graph and q:
            try:
                # KnowledgeGraph.search returns {"query": ..., "matches": [...]}.
                search_kwargs = (
                    {"allowed_workspaces": {workspace_id}}
                    if workspace_id is not None
                    else {}
                )
                hits = self._kg.search(q, limit, **search_kwargs).get("matches", [])
            except Exception as exc:
                LOGGER.exception("knowledge graph memory search failed")
                errors.append({"source": "graph", "detail": str(exc)})
                hits = []
            for hit in hits[:limit]:
                matched = _matched_terms(hit.get("title"), hit.get("name"), hit.get("summary"), hit.get("content"))
                results.append({
                    "source": "graph",
                    "id": hit.get("id") or hit.get("node_id"),
                    "title": hit.get("title") or hit.get("name") or "node",
                    "snippet": str(hit.get("summary") or hit.get("content") or "")[:240],
                    "kind": hit.get("type") or "node",
                    "score": _lexical_score(matched),
                    "matched_terms": matched,
                    **_visual_fields(hit),
                })

        # v9.3.0 hybrid recall: blend semantic similarity from the vector
        # index into the lexical ranking. Vector evidence lets recall find
        # knowledge phrased differently from the query — the main lexical
        # blind spot. The vector tier is optional: any failure degrades recall
        # back to pure lexical instead of breaking it.
        vector_used = False
        if self._enable_graph and q and hasattr(self._kg, "vector_search"):
            try:
                vector_hits = list(
                    self._kg.vector_search(q, limit=limit).get("matches", [])
                )
                # Workspace scoping is server-owned: the vector index is
                # global, so scoped calls must filter matches to visible
                # nodes before they can influence results.
                if workspace_id is not None and vector_hits and hasattr(self._kg, "filter_scoped_nodes"):
                    vector_hits = self._kg.filter_scoped_nodes(
                        vector_hits, {workspace_id}, id_key="node_id"
                    )
                vector_used = True
            except Exception as exc:
                LOGGER.exception("vector recall failed; falling back to lexical")
                errors.append({"source": "vector", "detail": str(exc)})
                vector_hits = []
            by_node_id = {str(r.get("id")): r for r in results if r.get("source") == "graph"}
            for hit in vector_hits:
                node_id = str(hit.get("node_id") or hit.get("id") or "")
                similarity = round(float(hit.get("score") or 0.0), 4)
                if similarity <= 0:
                    continue
                # Citation precision (v9.9.6): a chunk hit knows where in the
                # document it came from (section heading / page). Carry that
                # locator onto the recall row so the citation can say it.
                hit_metadata = hit.get("metadata")
                locator = (
                    str(hit_metadata.get("locator") or "")
                    if isinstance(hit_metadata, dict)
                    else ""
                )
                existing = by_node_id.get(node_id)
                if existing is not None:
                    existing["vector_score"] = max(existing.get("vector_score", 0.0), similarity)
                    existing["score"] = round(
                        max(existing.get("score", 0.0), 0.4 * existing.get("score", 0.0) + 0.6 * similarity),
                        4,
                    )
                    if locator and not existing.get("locator"):
                        existing["locator"] = locator
                else:
                    matched = _matched_terms(hit.get("title"), hit.get("summary"))
                    row = {
                        "source": "graph",
                        "id": node_id or hit.get("id"),
                        "title": hit.get("title") or "node",
                        "snippet": str(hit.get("summary") or "")[:240],
                        "kind": hit.get("type") or "node",
                        "score": round(max(_lexical_score(matched), 0.6 * similarity), 4),
                        "matched_terms": matched,
                        "vector_score": similarity,
                        **({"locator": locator} if locator else {}),
                        **_visual_fields(hit),
                    }
                    results.append(row)
                    if node_id:
                        by_node_id[node_id] = row

        # Quality gate: when at least one result carries real evidence
        # (lexical term hits, or semantic similarity in hybrid mode),
        # zero-score rows are noise relative to it and are dropped. When
        # nothing scores (e.g. tokenization mismatch), everything is kept so
        # the tiers' own search filters still decide — the gate never empties
        # a recall.
        candidates = len(results)
        if query_tokens and any(r.get("score", 0) > 0 for r in results):
            results = [r for r in results if r.get("score", 0) > 0]
        for r in results:
            r["confidence"] = "high" if r.get("score", 0) >= 0.65 else "medium" if r.get("score", 0) >= 0.3 else "low"
            evidence = []
            if r.get("matched_terms"):
                evidence.append("lexical")
            if r.get("vector_score"):
                evidence.append("semantic")
            r["evidence_kinds"] = evidence

        results.sort(key=lambda r: r.get("score", 0), reverse=True)
        return {
            "query": q,
            "results": results[: max(1, min(limit, 100))],
            "count": len(results),
            "source": "live",
            "status": "degraded" if errors else "ok",
            "errors": errors,
            "quality_gate": {
                "candidates": candidates,
                "passed": len(results),
                "filtered": candidates - len(results),
                "gate": "hybrid-evidence/v2" if vector_used else "lexical-evidence/v1",
            },
        }

    # ── inspect a single tier ─────────────────────────────────────────────
    def inspect(self, source: str, *, user_email: Optional[str] = None, workspace_id: Optional[str] = None, limit: int = 50) -> Dict[str, Any]:
        if source == "workspace":
            items = self._workspace_memories(user_email=user_email, workspace_id=workspace_id or "personal")[:limit]
            return {"source": source, "items": items, "count": len(items)}
        if source == "project":
            if workspace_id is None:
                items = [m for m in self._all_memories() if (m.get("workspace_id") or "personal") != "personal"][:limit]
            else:
                items = self._workspace_memories(user_email=user_email, workspace_id=workspace_id)[:limit]
            return {"source": source, "items": items, "count": len(items)}
        if source == "agent":
            items = self._snapshots(workspace_id=workspace_id)[:limit]
            return {"source": source, "items": items, "count": len(items)}
        if source == "conversation":
            convs = self._scoped_conversations(user_email=user_email, workspace_id=workspace_id)
            items = [{"id": c.get("id"), "title": c.get("title") or c.get("id"), "messages": len(c.get("messages") or [])} for c in convs[:limit]]
            return {"source": source, "items": items, "count": len(convs)}
        if source == "graph":
            return {"source": source, "stats": self._kg_stats() or {}, "available": bool(self._kg_stats())}
        if source == "vector":
            return {"source": source, "index": self._kg_index() or {}, "available": bool(self._kg_index())}
        raise KeyError(source)
