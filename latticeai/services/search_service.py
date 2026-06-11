"""Backend search orchestration for Lattice AI v3.

The service composes the existing knowledge graph, the local vector index, and
keyword search into UI-ready contracts without tying routers to store internals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional


DEFAULT_HYBRID_WEIGHTS = {
    "keyword": 0.35,
    "vector": 0.40,
    "graph": 0.25,
}


def _clean(text: Any, limit: int = 1000) -> str:
    return " ".join(str(text or "").split())[:limit]


def _result_key(result: Mapping[str, Any]) -> str:
    return str(result.get("id") or result.get("node_id") or "")


@dataclass
class SearchService:
    graph_store: Any

    def _require_graph(self) -> Any:
        if self.graph_store is None:
            raise ValueError("knowledge graph is disabled")
        return self.graph_store

    def _scope(self, matches, allowed_workspaces):
        """Drop matches scoped to workspaces the caller is not a member of
        (None = no scoping; legacy-global rows stay visible — documented)."""
        if allowed_workspaces is None:
            return matches
        graph = self._require_graph()
        return graph.filter_scoped_nodes(matches, allowed_workspaces)

    def keyword_search(self, query: str, *, limit: int = 30, allowed_workspaces=None) -> Dict[str, Any]:
        graph = self._require_graph()
        payload = graph.search(query, limit)
        matches = []
        for rank, match in enumerate(payload.get("matches", []), start=1):
            matches.append({
                "id": match["id"],
                "node_id": match["id"],
                "item_type": "node",
                "type": match.get("type"),
                "title": match.get("title"),
                "summary": _clean(match.get("summary")),
                "score": round(1.0 / rank, 6),
                "rank": rank,
                "sources": ["keyword"],
                "source_scores": {"keyword": round(1.0 / rank, 6)},
                "metadata": match.get("metadata") or {},
                "updated_at": match.get("updated_at"),
            })
        return {"query": query, "mode": "keyword", "matches": self._scope(matches, allowed_workspaces)}

    def vector_search(self, query: str, *, limit: int = 30, min_score: float = 0.0, allowed_workspaces=None) -> Dict[str, Any]:
        graph = self._require_graph()
        payload = graph.vector_search(query, limit=limit, min_score=min_score)
        matches = []
        for rank, match in enumerate(payload.get("matches", []), start=1):
            score = float(match.get("score") or 0.0)
            matches.append({
                "id": match.get("id"),
                "node_id": match.get("node_id"),
                "item_type": match.get("item_type"),
                "type": match.get("type"),
                "title": match.get("title"),
                "summary": _clean(match.get("summary")),
                "score": round(score, 6),
                "rank": rank,
                "sources": ["vector"],
                "source_scores": {"vector": round(score, 6)},
                "metadata": match.get("metadata") or {},
                "updated_at": match.get("updated_at"),
            })
        return {
            "query": query,
            "mode": "vector",
            "embedding_model": payload.get("embedding_model"),
            "embedding_dim": payload.get("embedding_dim"),
            "matches": self._scope(matches, allowed_workspaces),
        }

    def graph_search(self, query: str, *, limit: int = 30, expand_depth: int = 1, allowed_workspaces=None) -> Dict[str, Any]:
        graph = self._require_graph()
        limit = max(1, min(int(limit or 30), 100))
        expand_depth = max(0, min(int(expand_depth or 1), 3))
        direct = graph.search(query, limit=max(limit, 10)).get("matches", [])
        relationships = graph.relationship_search(query=query, limit=limit).get("relationships", [])
        by_id: Dict[str, Dict[str, Any]] = {}

        def add_node(node: Mapping[str, Any], score: float, reason: str, edge: Optional[Mapping[str, Any]] = None) -> None:
            node_id = str(node.get("id") or "")
            if not node_id:
                return
            current = by_id.get(node_id)
            if not current:
                current = {
                    "id": node_id,
                    "node_id": node_id,
                    "item_type": "node",
                    "type": node.get("type"),
                    "title": node.get("title"),
                    "summary": _clean(node.get("summary")),
                    "score": 0.0,
                    "sources": ["graph"],
                    "source_scores": {"graph": 0.0},
                    "metadata": node.get("metadata") or {},
                    "updated_at": node.get("updated_at"),
                    "graph_context": [],
                }
                by_id[node_id] = current
            current["score"] = max(float(current["score"]), score)
            current["source_scores"]["graph"] = max(float(current["source_scores"]["graph"]), score)
            context = {"reason": reason}
            if edge:
                context["relationship"] = {
                    "id": edge.get("id"),
                    "type": edge.get("type"),
                    "weight": edge.get("weight"),
                    "from": edge.get("from") or (edge.get("source") or {}).get("id"),
                    "to": edge.get("to") or (edge.get("target") or {}).get("id"),
                }
            current["graph_context"].append(context)

        for rank, match in enumerate(direct, start=1):
            add_node(match, 1.0 / rank, "direct_match")
            if expand_depth <= 0:
                continue
            try:
                neighborhood = graph.traverse(match["id"], depth=expand_depth, limit=limit * 3)
            except Exception:
                neighborhood = {"nodes": [], "edges": []}
            edge_by_pair = {
                (edge.get("from"), edge.get("to")): edge
                for edge in neighborhood.get("edges", [])
            }
            for node in neighborhood.get("nodes", []):
                if node.get("id") == match.get("id"):
                    continue
                related_edge = None
                for pair, edge in edge_by_pair.items():
                    if match.get("id") in pair and node.get("id") in pair:
                        related_edge = edge
                        break
                add_node(node, 0.45 / rank, "neighbor_expansion", related_edge)

        for rank, rel in enumerate(relationships, start=1):
            rel_score = 0.75 / rank
            add_node(rel.get("source") or {}, rel_score, "relationship_match", rel)
            add_node(rel.get("target") or {}, rel_score, "relationship_match", rel)

        matches = sorted(by_id.values(), key=lambda item: item["score"], reverse=True)[:limit]
        for rank, match in enumerate(matches, start=1):
            match["rank"] = rank
            match["score"] = round(float(match["score"]), 6)
            match["source_scores"]["graph"] = round(float(match["source_scores"]["graph"]), 6)
        return {"query": query, "mode": "graph", "expand_depth": expand_depth, "matches": self._scope(matches, allowed_workspaces)}

    def hybrid_search(
        self,
        query: str,
        *,
        limit: int = 30,
        keyword_limit: int = 30,
        vector_limit: int = 30,
        graph_limit: int = 30,
        weights: Optional[Mapping[str, float]] = None,
        allowed_workspaces=None,
    ) -> Dict[str, Any]:
        weights = {**DEFAULT_HYBRID_WEIGHTS, **dict(weights or {})}
        channels = {
            "keyword": self.keyword_search(query, limit=keyword_limit),
            "vector": self.vector_search(query, limit=vector_limit),
            "graph": self.graph_search(query, limit=graph_limit),
        }
        fused: Dict[str, Dict[str, Any]] = {}
        for source, payload in channels.items():
            source_weight = float(weights.get(source, 0.0))
            for rank, result in enumerate(payload.get("matches", []), start=1):
                key = _result_key(result)
                if not key:
                    continue
                source_score = float((result.get("source_scores") or {}).get(source, result.get("score") or 0.0))
                rank_score = 1.0 / rank
                contribution = source_weight * max(source_score, rank_score)
                current = fused.get(key)
                if not current:
                    current = {
                        **result,
                        "sources": [],
                        "source_scores": {},
                        "score": 0.0,
                    }
                    fused[key] = current
                current["score"] = float(current["score"]) + contribution
                if source not in current["sources"]:
                    current["sources"].append(source)
                current["source_scores"][source] = round(source_score, 6)
                if result.get("graph_context"):
                    current.setdefault("graph_context", [])
                    current["graph_context"].extend(result.get("graph_context") or [])

        matches = self._scope(sorted(fused.values(), key=lambda item: item["score"], reverse=True), allowed_workspaces)[: max(1, min(limit, 100))]
        for rank, match in enumerate(matches, start=1):
            match["rank"] = rank
            match["score"] = round(float(match["score"]), 6)
            match["fusion"] = {
                "weights": weights,
                "sources": match.get("sources", []),
            }
        return {
            "query": query,
            "mode": "hybrid",
            "weights": weights,
            "channels": {
                name: {
                    key: value
                    for key, value in payload.items()
                    if key not in {"matches"}
                }
                for name, payload in channels.items()
            },
            "matches": matches,
        }

    def graph(self, *, limit: int = 300) -> Dict[str, Any]:
        return self._require_graph().graph(limit=limit)

    def node(self, node_id: str, *, include_neighbors: bool = True, depth: int = 1, limit: int = 100) -> Dict[str, Any]:
        graph = self._require_graph()
        payload = {"node": graph.get_node(node_id)}
        if include_neighbors:
            payload["neighborhood"] = graph.traverse(node_id, depth=depth, limit=limit)
        return payload

    def relationships(
        self,
        *,
        query: str = "",
        node_id: str = "",
        relationship_type: str = "",
        limit: int = 30,
    ) -> Dict[str, Any]:
        return self._require_graph().relationship_search(
            query=query,
            node_id=node_id,
            relationship_type=relationship_type,
            limit=limit,
        )

    def index_status(self) -> Dict[str, Any]:
        return self._require_graph().index_status()

    def embeddings_status(
        self,
        *,
        resolved: Optional[Mapping[str, Any]] = None,
        refresh: bool = False,
    ) -> Dict[str, Any]:
        """Report the active embedding provider for the Models → Embeddings UI.

        Combines the resolved-provider info (requested vs active, fallback,
        health) with the vector index's identity and last build time. The
        ``state`` is one of ``production`` | ``fallback`` | ``unavailable`` so
        the UI never shows a down provider as live.
        """
        resolved = dict(resolved or {})
        graph = self.graph_store
        embedder = getattr(graph, "_embedding_model", None)

        meta: Dict[str, Any] = {}
        if embedder is not None and hasattr(embedder, "metadata"):
            try:
                meta = dict(embedder.metadata())
            except Exception:
                meta = {}
        else:  # legacy LocalEmbeddingModel
            meta = {
                "provider": "hash",
                "model": getattr(embedder, "model_id", "lattice-local-hash-v1"),
                "model_id": getattr(embedder, "model_id", "lattice-local-hash-v1"),
                "dim": getattr(embedder, "dim", 384),
                "grade": "fallback",
            }

        health = resolved.get("health") or {"status": "unknown", "detail": ""}
        if refresh and embedder is not None and hasattr(embedder, "health"):
            try:
                health = embedder.health()
            except Exception as exc:  # pragma: no cover - defensive
                health = {"status": "unavailable", "detail": str(exc)}

        fell_back = bool(resolved.get("fell_back"))
        grade = str(meta.get("grade") or ("fallback" if fell_back else "production"))
        if fell_back or health.get("status") == "unavailable":
            state = "unavailable" if fell_back else "fallback"
        else:
            state = "fallback" if grade == "fallback" else "production"

        index: Dict[str, Any] = {}
        last_indexed_at = None
        if graph is not None:
            try:
                status = graph.index_status()
                index = {
                    "status": status.get("status"),
                    "source_items": status.get("source_items"),
                    "indexed_items": status.get("indexed_items"),
                    "ready_items": status.get("ready_items"),
                    "pending_items": status.get("pending_items"),
                    "stale_items": status.get("stale_items"),
                    "embedding_model": (status.get("storage") or {}).get("embedding_model"),
                    "embedding_dim": (status.get("storage") or {}).get("embedding_dim"),
                }
                for op in status.get("operations", []):
                    if op.get("status") == "completed" and op.get("completed_at"):
                        last_indexed_at = op.get("completed_at")
                        break
            except Exception as exc:  # pragma: no cover - defensive
                index = {"error": str(exc)}

        return {
            "provider": meta.get("provider"),
            "requested_provider": resolved.get("requested_provider") or meta.get("provider"),
            "active_provider": resolved.get("active_provider") or meta.get("provider"),
            "model": meta.get("model"),
            "model_id": meta.get("model_id"),
            "dimensions": meta.get("dim"),
            "grade": grade,
            "state": state,
            "fell_back": fell_back,
            "health": health,
            "detail": resolved.get("detail", ""),
            "last_indexed_at": last_indexed_at,
            "index": index,
            "available_providers": list(resolved.get("available_providers") or []),
        }

    def rebuild_index(self, *, full: bool = False, include_nodes: bool = True, include_chunks: bool = True) -> Dict[str, Any]:
        return self._require_graph().rebuild_vector_index(
            full=full,
            include_nodes=include_nodes,
            include_chunks=include_chunks,
        )
