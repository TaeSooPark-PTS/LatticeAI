"""Relationship explorer: what a node connects to, and the way between two.

Extracted from ``WorkspaceOSStore``. Pure graph reading — it touches no state
file and records no events, so it holds no reference to the store at all.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Dict, List, Optional

from latticeai.core.quiet import quiet

__all__ = ["WorkspaceRelationships", "shortest_path"]


def shortest_path(edges: List[Dict[str, Any]], start: str, target: Optional[str]) -> List[str]:
    """Fewest-hops path between two nodes, treating every edge as undirected.

    Breadth-first, so the first arrival at ``target`` is a shortest one.
    Returns ``[]`` when either end is missing or nothing connects them.
    """
    if not start or not target:
        return []
    adjacency: Dict[str, List[str]] = {}
    for edge in edges:
        src = edge.get("from")
        dst = edge.get("to")
        if src and dst:
            adjacency.setdefault(src, []).append(dst)
            adjacency.setdefault(dst, []).append(src)
    queue: deque[List[str]] = deque([[start]])
    seen = {start}
    while queue:
        path = queue.popleft()
        node = path[-1]
        if node == target:
            return path
        for neighbor in adjacency.get(node, []):
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append(path + [neighbor])
    return []


class WorkspaceRelationships:
    """Answers "what is this connected to" for one node of the graph."""

    def __init__(self, store: Any) -> None:
        self.store = store

    def explore(
        self,
        graph: Any,
        node_id: str,
        target_id: Optional[str] = None,
        limit: int = 500,
    ) -> Dict[str, Any]:
        if graph is None:
            return {
                "node_id": node_id,
                "inbound": [],
                "outbound": [],
                "related_entities": [],
                "shortest_path": [],
            }
        data = graph.graph(limit=limit)
        nodes = {node.get("id"): node for node in data.get("nodes") or [] if node.get("id")}
        edges = data.get("edges") or []
        inbound = [edge for edge in edges if edge.get("to") == node_id]
        outbound = [edge for edge in edges if edge.get("from") == node_id]
        # The capped `graph(limit=…)` window may not contain this node at all.
        # Asking the store for its neighbours directly is the difference between
        # "not connected" and "outside the page we happened to fetch".
        if node_id not in nodes:
            try:
                neighbors = graph.neighbors(node_id)
                for node in neighbors.get("neighbors") or []:
                    nodes[node.get("id")] = node
                edges.extend(neighbors.get("edges") or [])
                inbound = [edge for edge in edges if edge.get("to") == node_id]
                outbound = [edge for edge in edges if edge.get("from") == node_id]
            except Exception:
                quiet()

        related_ids = []
        for edge in inbound + outbound:
            other = edge.get("from") if edge.get("to") == node_id else edge.get("to")
            if other:
                related_ids.append(other)
        related = [nodes.get(rid, {"id": rid}) for rid in dict.fromkeys(related_ids)]
        return {
            "node_id": node_id,
            "node": nodes.get(node_id, {"id": node_id}),
            "inbound": inbound,
            "outbound": outbound,
            "related_entities": related,
            "shortest_path": shortest_path(edges, node_id, target_id) if target_id else [],
        }
