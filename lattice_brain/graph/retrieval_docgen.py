from __future__ import annotations

from typing import TYPE_CHECKING

# ruff: noqa: F403,F405
from ._kg_common import *  # noqa: F403,F401

# The cross-mixin surface (`_connect`, `_upsert_node`, …) is declared in
# `_kg_contract.KnowledgeGraphCore`. It is a typing-only base: at runtime this
# is `object`, so the MRO of `KnowledgeGraphStore` is unchanged.
if TYPE_CHECKING:
    from ._kg_contract import KnowledgeGraphCore as _Core
else:
    _Core = object



class KnowledgeGraphDocGenMixin(_Core):
    """Multi-hop retrieval specialised for document generation, split out
    of retrieval. Composed into KnowledgeGraphStore alongside the other
    retrieval mixins; shared instance means sibling helpers resolve via MRO.
    """

    def search_for_document_generation(
        self,
        query: str,
        limit: int = 10,
        *,
        allowed_workspaces=None,
        include_legacy_global: bool = False,
    ) -> List[Dict[str, Any]]:
        """Hybrid retrieval optimized for document generation.

        Scoring: 0.5*text_relevance + 0.3*graph_relationship + 0.2*recency
        Returns nodes with rich context for document generation prompts.
        """
        query = str(query or "").strip()
        if not query:
            return []
        limit = max(1, min(int(limit or 10), 50))
        terms = _topic_candidates(query, limit=12)
        now = datetime.now()
        nt, et = self._read_tables()

        with self._connect() as conn:
            candidate_rows = []
            seen_ids = set()

            # `query` is non-empty here — the early return above took the blank case.
            q = f"%{query}%"
            rows = conn.execute(
                f"""
                    SELECT id, type, title, summary, metadata_json, updated_at
                    FROM {nt}
                    WHERE (title LIKE ? OR summary LIKE ? OR metadata_json LIKE ?)
                      AND type IN ('Document', 'File', 'CodeFile', 'SlideDeck',
                                   'Spreadsheet', 'Image', 'ImageText', 'Chat',
                                   'Decision', 'Task', 'Concept', 'Feature',
                                   'Page', 'Slide')
                    ORDER BY updated_at DESC, id ASC
                    LIMIT ?
                    """,
                (q, q, q, limit * 5),
            ).fetchall()
            for row in rows:
                if row["id"] not in seen_ids:
                    seen_ids.add(row["id"])
                    candidate_rows.append(row)

            for term in terms:
                t = f"%{term}%"
                rows = conn.execute(
                    f"""
                        SELECT id, type, title, summary, metadata_json, updated_at
                        FROM {nt}
                        WHERE (title LIKE ? OR summary LIKE ? OR metadata_json LIKE ?)
                          AND type IN ('Document', 'File', 'CodeFile', 'SlideDeck',
                                       'Spreadsheet', 'Image', 'ImageText', 'Chat',
                                       'Decision', 'Task', 'Concept', 'Feature',
                                       'Page', 'Slide')
                        ORDER BY updated_at DESC, id ASC
                        LIMIT ?
                        """,
                    (t, t, t, limit * 3),
                ).fetchall()
                for row in rows:
                    if row["id"] not in seen_ids:
                        seen_ids.add(row["id"])
                        candidate_rows.append(row)

            scored_results = []
            for row in candidate_rows:
                haystack = (
                    f"{row['title']} {row['summary']} {row['metadata_json']}".lower()
                )

                text_hits = sum(1 for term in terms if term.lower() in haystack)
                text_score = min(1.0, text_hits / max(len(terms), 1))

                edge_count = conn.execute(
                    f"SELECT COUNT(*) AS c FROM {et} WHERE from_node=? OR to_node=?",
                    (row["id"], row["id"]),
                ).fetchone()["c"]
                graph_score = min(1.0, math.log1p(edge_count) / 4.0)

                recency = _recency_score(
                    row["updated_at"], now=now, half_life_days=14.0
                )

                doc_type_boost = (
                    1.2
                    if row["type"]
                    in (
                        "Document",
                        "File",
                        "SlideDeck",
                        "Decision",
                    )
                    else 1.0
                )

                hybrid_score = (
                    0.5 * text_score + 0.3 * graph_score + 0.2 * recency
                ) * doc_type_boost

                meta = _safe_loads(row["metadata_json"])
                neighbor_concepts = []
                neighbor_rows = conn.execute(
                    f"""
                        SELECT n.id, n.title, n.type FROM {et} e
                        JOIN {nt} n ON n.id = CASE WHEN e.from_node = ? THEN e.to_node ELSE e.from_node END
                        WHERE (e.from_node = ? OR e.to_node = ?)
                          AND n.type IN ('Concept', 'Feature', 'Decision', 'Task')
                        LIMIT 8
                        """,
                    (row["id"], row["id"], row["id"]),
                ).fetchall()
                for nr in neighbor_rows:
                    neighbor_concepts.append({"id": nr["id"], "title": nr["title"], "type": nr["type"]})

                scored_results.append(
                    {
                        "id": row["id"],
                        "type": row["type"],
                        "title": row["title"],
                        "summary": row["summary"],
                        "metadata": meta,
                        "updated_at": row["updated_at"],
                        "hybrid_score": round(hybrid_score, 4),
                        "scores": {
                            "text": round(text_score, 4),
                            "graph": round(graph_score, 4),
                            "recency": round(recency, 4),
                        },
                        "related_concepts": neighbor_concepts,
                    }
                )

            if allowed_workspaces is not None:
                scored_results = self.filter_scoped_nodes(
                    scored_results,
                    allowed_workspaces,
                    include_legacy_global=include_legacy_global,
                )
                for item in scored_results:
                    item["related_concepts"] = self.filter_scoped_nodes(
                        item.get("related_concepts", []),
                        allowed_workspaces,
                        include_legacy_global=include_legacy_global,
                    )
            scored_results.sort(key=lambda x: x["hybrid_score"], reverse=True)
            return scored_results[:limit]

    def multi_hop_context(
        self,
        node_ids: List[str],
        max_hops: int = 2,
        *,
        allowed_workspaces=None,
        include_legacy_global: bool = False,
    ) -> Dict[str, Any]:
        """Multi-hop graph traversal from seed nodes for richer context."""
        visited_nodes = set()
        visited_edges = set()
        all_nodes = []
        all_edges = []
        frontier = set(node_ids)
        nt, et = self._read_tables()

        with self._connect() as conn:
            for hop in range(max_hops):
                if not frontier:
                    break
                next_frontier = set()
                for nid in frontier:
                    if nid in visited_nodes:
                        continue
                    visited_nodes.add(nid)
                    row = conn.execute(
                        f"SELECT id, type, title, summary, metadata_json, updated_at FROM {nt} WHERE id=?",
                        (nid,),
                    ).fetchone()
                    if row:
                        all_nodes.append(
                            {
                                "id": row["id"],
                                "type": row["type"],
                                "title": row["title"],
                                "summary": row["summary"],
                                "metadata": _safe_loads(row["metadata_json"]),
                                "hop": hop,
                            }
                        )
                    edge_rows = conn.execute(
                        f"""
                            SELECT id, from_node, to_node, type, weight
                            FROM {et} WHERE from_node=? OR to_node=?
                            ORDER BY id ASC
                            """,
                        (nid, nid),
                    ).fetchall()
                    for er in edge_rows:
                        if er["id"] not in visited_edges:
                            visited_edges.add(er["id"])
                            all_edges.append(
                                {
                                    "from": er["from_node"],
                                    "to": er["to_node"],
                                    "type": er["type"],
                                    "weight": er["weight"],
                                }
                            )
                            other = (
                                er["to_node"]
                                if er["from_node"] == nid
                                else er["from_node"]
                            )
                            if other not in visited_nodes:
                                next_frontier.add(other)
                frontier = next_frontier

        if allowed_workspaces is not None:
            all_nodes = self.filter_scoped_nodes(
                all_nodes,
                allowed_workspaces,
                include_legacy_global=include_legacy_global,
            )
            visible_ids = {node.get("id") for node in all_nodes}
            all_edges = [
                edge for edge in all_edges
                if edge.get("from") in visible_ids and edge.get("to") in visible_ids
            ]
        return {"nodes": all_nodes, "edges": all_edges}
