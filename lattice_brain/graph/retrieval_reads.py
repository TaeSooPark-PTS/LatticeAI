"""Non-search read surface of the knowledge graph (v9.9.5 decomposition).

Byte-identical move-only split out of :mod:`lattice_brain.graph.retrieval`,
which now keeps the search surface (``search`` / ``hybrid_search`` /
``context_for_query``). This mixin owns the document listing, workspace
scoping reads, node/neighbor/relationship lookups, graph traversal, and
store statistics. Mixins share ``self`` on
:class:`lattice_brain.graph.store.KnowledgeGraphStore`, so cross-mixin
calls (e.g. ``search`` → ``filter_scoped_nodes``) behave exactly as before.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..quiet import quiet

# ruff: noqa: F403,F405
from ._kg_common import *  # noqa: F403,F401
from .schema import TEMPORAL_PREDICATE_SQL

# The cross-mixin surface (`_connect`, `_upsert_node`, …) is declared in
# `_kg_contract.KnowledgeGraphCore`. It is a typing-only base: at runtime this
# is `object`, so the MRO of `KnowledgeGraphStore` is unchanged.
if TYPE_CHECKING:
    from ._kg_contract import KnowledgeGraphCore as _Core
else:
    _Core = object


def _as_of_stamp(value: Any) -> str:
    """Normalize an ``as_of`` argument into the store's own timestamp format.

    The store writes naive local ISO-8601 seconds (``lattice_brain.utils.now_iso``),
    so validity comparisons are plain lexicographic string comparisons against
    that one format. An aware datetime is converted to local time first rather
    than compared with an offset suffix the stored rows never carry.
    """
    if isinstance(value, datetime):
        moment = value.astimezone().replace(tzinfo=None) if value.tzinfo else value
        return moment.isoformat(timespec="seconds")
    text = str(value or "").strip()
    if not text:
        raise ValueError("as_of requires a timestamp")
    return text


def _record_access(db_path: Any, node_id: str) -> None:
    """Record one access on a node (the importance/decay input).

    Runs on its own short transaction, deliberately *outside* the read that
    triggered it: promoting every node read to a writer would let a concurrent
    ingest's write lock fail an otherwise perfectly good read. The counter is a
    nice-to-have, so every sqlite failure — a missing row, a missing column, a
    busy database — is swallowed.
    """
    if KGStoreV2 is None:
        return
    try:
        KGStoreV2(db_path).touch_node(node_id)
    except sqlite3.Error:
        quiet()


class KnowledgeGraphReadsMixin(_Core):
    def list_documents(self, limit: int = 200) -> Dict[str, Any]:
        """List ingested ``Document`` nodes with their ingest + index state.

        Powers the Files view: every accepted upload and every indexed local
        document becomes a ``Document`` node. A document is reported ``indexed``
        once its retrieval chunks exist (searchable in Chat / Hybrid Search).
        """
        limit = max(1, min(int(limit or 200), 1000))
        nt, _ = self._read_tables()
        documents: List[Dict[str, Any]] = []
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT id, title, summary, metadata_json, created_at, updated_at "
                f"FROM {nt} WHERE type='Document' ORDER BY updated_at DESC, id ASC LIMIT ?",
                (limit,),
            ).fetchall()
            for row in rows:
                meta = _safe_loads(row["metadata_json"]) or {}
                extracted = meta.get("extracted") or {}
                node_id = row["id"]
                chunk_count = conn.execute(
                    "SELECT COUNT(*) AS c FROM chunks WHERE source_node=?",
                    (node_id,),
                ).fetchone()["c"]
                if not chunk_count:
                    # Legacy projections represented chunks as graph nodes and
                    # linked them only through metadata_json. Keep read
                    # compatibility without making the fragile LIKE path the
                    # primary query.
                    chunk_count = conn.execute(
                        f"SELECT COUNT(*) AS c FROM {nt} WHERE type='Chunk' AND metadata_json LIKE ?",
                        (f"%{node_id}%",),
                    ).fetchone()["c"]
                documents.append(
                    {
                        "id": node_id,
                        "filename": meta.get("filename") or row["title"],
                        "ext": meta.get("ext"),
                        "mime_type": meta.get("mime_type"),
                        "bytes": meta.get("bytes"),
                        "sha256": meta.get("sha256"),
                        "uploader": meta.get("uploader"),
                        "chars": extracted.get("chars"),
                        "chunks": int(chunk_count or 0),
                        "indexed": int(chunk_count or 0) > 0,
                        "ingest_state": "indexed"
                        if int(chunk_count or 0) > 0
                        else "ingested",
                        "created_at": row["created_at"],
                        "updated_at": row["updated_at"],
                    }
                )
        return {
            "documents": documents,
            "total": len(documents),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }

    def workspaces_of(self, node_ids) -> Dict[str, Optional[str]]:
        """Map known node ids to their workspace scope.

        ``None`` is returned only for a row that is explicitly present in the
        authoritative v2 projection with a NULL workspace.  Missing ids remain
        missing, and projection/query failures propagate so callers can fail
        closed instead of mistaking every candidate for legacy-global data.
        """
        ids = [str(i) for i in node_ids if i]
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as conn:
            return {
                row["id"]: row["workspace_id"]
                for row in conn.execute(
                    f"SELECT id, workspace_id FROM nodes_v2 WHERE id IN ({placeholders})",
                    ids,
                ).fetchall()
            }

    def filter_scoped_nodes(
        self,
        items,
        allowed_workspaces,
        *,
        id_key: str = "id",
        include_legacy_global: bool = False,
    ):
        """Drop items scoped to a workspace the caller is not a member of.

        ``allowed_workspaces=None`` means no scoping (single-user / no-auth
        mode). In scoped/multi-user mode, unknown ids are private and
        legacy-global rows require the explicit ``include_legacy_global=True``
        compatibility opt-in.
        """
        candidates = list(items)
        if allowed_workspaces is None:
            return candidates
        allowed = {str(workspace_id) for workspace_id in allowed_workspaces if workspace_id}
        scopes = self.workspaces_of([item.get(id_key) for item in candidates])
        visible = []
        for item in candidates:
            node_id = str(item.get(id_key) or "")
            if not node_id or node_id not in scopes:
                # Unknown/unprojected rows are never treated as public.
                continue
            workspace_id = scopes[node_id]
            if workspace_id is None:
                if include_legacy_global:
                    visible.append(item)
            elif str(workspace_id) in allowed:
                visible.append(item)
        return visible

    @staticmethod
    def _workspace_scope_sql(
        allowed_workspaces,
        include_legacy_global: bool,
    ) -> Tuple[Optional[str], List[Any]]:
        """``nodes_v2`` predicate for a caller's scope, or ``(None, [])``.

        ``None`` means "no scoping" and is the unscoped single-user path.
        An *empty* allowed set is not the same thing: it is a caller who may
        read nothing, so it yields a predicate that matches nothing rather
        than silently degrading into the unscoped query.
        """
        if allowed_workspaces is None:
            return None, []
        allowed = sorted({str(item) for item in allowed_workspaces if item})
        clauses: List[str] = []
        params: List[Any] = []
        if allowed:
            placeholders = ",".join("?" for _ in allowed)
            clauses.append(f"workspace_id IN ({placeholders})")
            params.extend(allowed)
        if include_legacy_global:
            clauses.append("workspace_id IS NULL")
        if not clauses:
            return "0", []
        return " OR ".join(clauses), params

    def neighbors(
        self,
        node_id: str,
        *,
        allowed_workspaces=None,
        include_legacy_global: bool = False,
        as_of=None,
    ) -> Dict[str, Any]:
        """Return direct neighbors (1-hop) of a node.

        ``as_of`` is additive and defaults to ``None`` — the historical call
        keeps its exact behaviour. With a timestamp, neighbors whose validity
        window does not cover that instant are dropped (and with them the edges
        that would dangle).
        """
        if allowed_workspaces is not None and not self.filter_scoped_nodes(
            [{"id": node_id}],
            allowed_workspaces,
            include_legacy_global=include_legacy_global,
        ):
            raise ValueError(f"graph node not found: {node_id}")
        stamp = None if as_of is None else _as_of_stamp(as_of)
        nt, et = self._read_tables()
        with self._connect() as conn:
            edge_rows = conn.execute(
                f"SELECT from_node, to_node, type, weight FROM {et} WHERE from_node=? OR to_node=? ORDER BY id ASC",
                (node_id, node_id),
            ).fetchall()
            neighbor_ids: set = set()
            edges = []
            for row in edge_rows:
                neighbor_ids.add(row["from_node"])
                neighbor_ids.add(row["to_node"])
                edges.append(
                    {
                        "from": row["from_node"],
                        "to": row["to_node"],
                        "type": row["type"],
                        "weight": row["weight"],
                    }
                )
            neighbor_ids.discard(node_id)
            nodes = []
            if neighbor_ids:
                placeholders = ",".join("?" * len(neighbor_ids))
                nodes = [
                    {
                        "id": row["id"],
                        "type": row["type"],
                        "title": row["title"],
                        "summary": row["summary"],
                        "metadata": _safe_loads(row["metadata_json"]),
                    }
                    for row in conn.execute(
                        f"SELECT id, type, title, summary, metadata_json FROM {nt} WHERE id IN ({placeholders}) ORDER BY id ASC",
                        list(neighbor_ids),
                    )
                ]
            if stamp is not None:
                valid = self._valid_node_ids_at(conn, stamp)
                nodes = [node for node in nodes if str(node.get("id")) in valid]
                kept = {str(node.get("id")) for node in nodes} | {node_id}
                edges = [
                    edge for edge in edges
                    if edge.get("from") in kept and edge.get("to") in kept
                ]
        if allowed_workspaces is not None:
            nodes = self.filter_scoped_nodes(
                nodes,
                allowed_workspaces,
                include_legacy_global=include_legacy_global,
            )
            kept = {node.get("id") for node in nodes}
            edges = [
                edge for edge in edges
                if (edge.get("from") == node_id or edge.get("from") in kept)
                and (edge.get("to") == node_id or edge.get("to") in kept)
            ]
        return {"node_id": node_id, "neighbors": nodes, "edges": edges}

    def get_node(
        self,
        node_id: str,
        *,
        allowed_workspaces=None,
        include_legacy_global: bool = False,
    ) -> Dict[str, Any]:
        node_id = str(node_id or "").strip()
        if not node_id:
            raise ValueError("node_id required")
        nt, et = self._read_tables()
        with self._connect() as conn:
            row = conn.execute(
                f"""
                    SELECT id, type, title, summary, metadata_json, updated_at
                    FROM {nt}
                    WHERE id=?
                    """,
                (node_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"graph node not found: {node_id}")
            degree = conn.execute(
                f"SELECT COUNT(*) AS c FROM {et} WHERE from_node=? OR to_node=?",
                (node_id, node_id),
            ).fetchone()["c"]
        # Fetching a node by id is the one unambiguous "this memory was used"
        # signal the read path has; it feeds importance_report(). Recorded
        # after the read transaction closes so it can never fail the read.
        _record_access(self.db_path, node_id)
        node = {
            "id": row["id"],
            "type": row["type"],
            "title": row["title"],
            "summary": row["summary"],
            "metadata": _safe_loads(row["metadata_json"]),
            "updated_at": row["updated_at"],
            "degree": degree,
        }
        if allowed_workspaces is not None and not self.filter_scoped_nodes(
            [node],
            allowed_workspaces,
            include_legacy_global=include_legacy_global,
        ):
            raise ValueError(f"graph node not found: {node_id}")
        return node

    # ── temporal reads (v11.1.0) ─────────────────────────────────────────
    #
    # Validity lives on ``nodes_v2``/``edges_v2``, the authoritative v2
    # projection — the same table ``workspaces_of`` already reads for scoping,
    # and the only one that carries the columns whichever read mode
    # ``_read_tables()`` is in. The legacy compatibility tables have no
    # temporal dimension, so slicing them would silently answer "everything".

    def _valid_node_ids_at(self, conn: sqlite3.Connection, stamp: str) -> set:
        """Ids of nodes whose validity window covers ``stamp``."""
        return {
            row["id"]
            for row in conn.execute(
                f"SELECT id FROM nodes_v2 WHERE {TEMPORAL_PREDICATE_SQL}",
                (stamp, stamp),
            ).fetchall()
        }

    def access_stats(self, node_ids=None) -> Dict[str, Dict[str, Any]]:
        """Per-node access bookkeeping read from the authoritative projection.

        ``{node_id: {"accesses": float, "last_used": str | None}}`` for the ids
        requested (all nodes when ``node_ids`` is ``None``). Counts come from
        ``_touch_node``; a node never opened simply reports ``0.0``.
        """
        query = "SELECT id, importance_score, last_used FROM nodes_v2"
        params: List[Any] = []
        if node_ids is not None:
            ids = sorted({str(node_id) for node_id in node_ids if node_id})
            if not ids:
                return {}
            query += f" WHERE id IN ({','.join('?' * len(ids))})"
            params = list(ids)
        with self._connect() as conn:
            return {
                row["id"]: {
                    "accesses": float(row["importance_score"] or 0.0),
                    "last_used": row["last_used"],
                }
                for row in conn.execute(query, params).fetchall()
            }

    def as_of(
        self,
        timestamp,
        *,
        limit: int = 300,
        allowed_workspaces=None,
        include_legacy_global: bool = False,
    ) -> Dict[str, Any]:
        """The graph slice that was valid at ``timestamp``.

        Answers "what did I know in June 2025?": nodes and edges whose
        ``[valid_from, valid_to)`` window covers the instant, with
        ``valid_from`` falling back to ``created_at`` so a Brain that predates
        the temporal columns reads as "always was true" rather than as empty.
        Edges are returned only when *both* endpoints are in the slice.
        """
        stamp = _as_of_stamp(timestamp)
        # An explicit 0 clamps to 1 rather than re-expanding to the default:
        # `limit or 300` would quietly return 300 rows to a caller who asked
        # for none.
        try:
            limit = max(1, min(int(limit), 2000))
        except (TypeError, ValueError):
            limit = 300
        scope_sql, scope_params = self._workspace_scope_sql(
            allowed_workspaces, include_legacy_global
        )
        where = [TEMPORAL_PREDICATE_SQL]
        params: List[Any] = [stamp, stamp]
        if scope_sql is not None:
            where.append(f"({scope_sql})")
            params.extend(scope_params)
        with self._connect() as conn:
            node_rows = conn.execute(
                "SELECT id, COALESCE(legacy_type, type) AS type, label AS title, "
                "summary, attrs AS metadata_json, updated_at, "
                "valid_from, valid_to, superseded_by "
                f"FROM nodes_v2 WHERE {' AND '.join(where)} "
                "ORDER BY updated_at DESC, id ASC LIMIT ?",
                (*params, limit),
            ).fetchall()
            nodes = [
                {
                    "id": row["id"],
                    "type": row["type"],
                    "title": row["title"],
                    "summary": row["summary"],
                    "metadata": _safe_loads(row["metadata_json"]),
                    "updated_at": row["updated_at"],
                    "valid_from": row["valid_from"],
                    "valid_to": row["valid_to"],
                    "superseded_by": row["superseded_by"],
                }
                for row in node_rows
            ]
            edges: List[Dict[str, Any]] = []
            visible = sorted(str(node["id"]) for node in nodes)
            if visible:
                placeholders = ",".join("?" * len(visible))
                edges = [
                    {
                        "id": row["id"],
                        "from": row["from_node"],
                        "to": row["to_node"],
                        "type": row["type"],
                        "weight": row["weight"],
                        "metadata": _safe_loads(row["metadata_json"]),
                        "valid_from": row["valid_from"],
                        "valid_to": row["valid_to"],
                        "superseded_by": row["superseded_by"],
                    }
                    for row in conn.execute(
                        "SELECT id, source AS from_node, target AS to_node, "
                        "COALESCE(legacy_type, type) AS type, weight, "
                        "metadata AS metadata_json, valid_from, valid_to, superseded_by "
                        f"FROM edges_v2 WHERE {TEMPORAL_PREDICATE_SQL} "
                        f"AND source IN ({placeholders}) AND target IN ({placeholders}) "
                        "ORDER BY weight DESC, created_at DESC, id ASC",
                        (stamp, stamp, *visible, *visible),
                    ).fetchall()
                ]
        return {
            "as_of": stamp,
            "nodes": nodes,
            "edges": edges,
            "node_count": len(nodes),
            "edge_count": len(edges),
        }

    def relationship_search(
        self,
        *,
        query: str = "",
        node_id: str = "",
        relationship_type: str = "",
        limit: int = 30,
        allowed_workspaces=None,
        include_legacy_global: bool = False,
    ) -> Dict[str, Any]:
        query = str(query or "").strip()
        node_id = str(node_id or "").strip()
        relationship_type = str(relationship_type or "").strip()
        limit = max(1, min(int(limit or 30), 200))
        nt, et = self._read_tables()
        where = []
        params: List[Any] = []
        if node_id:
            where.append("(e.from_node=? OR e.to_node=?)")
            params.extend([node_id, node_id])
        if relationship_type:
            where.append("e.type LIKE ?")
            params.append(f"%{relationship_type}%")
        if query:
            where.append(
                "(e.type LIKE ? OR e.metadata_json LIKE ? OR src.title LIKE ? OR dst.title LIKE ? OR src.summary LIKE ? OR dst.summary LIKE ?)"
            )
            params.extend([f"%{query}%"] * 6)
        where_sql = "WHERE " + " AND ".join(where) if where else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                    SELECT
                      e.id, e.from_node, e.to_node, e.type, e.weight, e.metadata_json, e.created_at,
                      src.type AS source_type, src.title AS source_title, src.summary AS source_summary,
                      src.metadata_json AS source_metadata,
                      dst.type AS target_type, dst.title AS target_title, dst.summary AS target_summary,
                      dst.metadata_json AS target_metadata
                    FROM {et} e
                    JOIN {nt} src ON src.id=e.from_node
                    JOIN {nt} dst ON dst.id=e.to_node
                    {where_sql}
                    ORDER BY e.weight DESC, e.created_at DESC, e.id ASC
                    LIMIT ?
                    """,
                (*params, limit),
            ).fetchall()
        relationships = [
                {
                    "id": row["id"],
                    "type": row["type"],
                    "weight": row["weight"],
                    "metadata": _safe_loads(row["metadata_json"]),
                    "created_at": row["created_at"],
                    "source": {
                        "id": row["from_node"],
                        "type": row["source_type"],
                        "title": row["source_title"],
                        "summary": row["source_summary"],
                        "metadata": _safe_loads(row["source_metadata"]),
                    },
                    "target": {
                        "id": row["to_node"],
                        "type": row["target_type"],
                        "title": row["target_title"],
                        "summary": row["target_summary"],
                        "metadata": _safe_loads(row["target_metadata"]),
                    },
                }
                for row in rows
            ]
        if allowed_workspaces is not None:
            kept = []
            for rel in relationships:
                endpoints = [
                    {"id": (rel.get("source") or {}).get("id")},
                    {"id": (rel.get("target") or {}).get("id")},
                ]
                if len(
                    self.filter_scoped_nodes(
                        endpoints,
                        allowed_workspaces,
                        include_legacy_global=include_legacy_global,
                    )
                ) == 2:
                    kept.append(rel)
            relationships = kept
        return {
            "query": query,
            "node_id": node_id,
            "relationship_type": relationship_type,
            "relationships": relationships,
        }

    def traverse(
        self,
        node_id: str,
        *,
        depth: int = 1,
        limit: int = 100,
        allowed_workspaces=None,
        include_legacy_global: bool = False,
    ) -> Dict[str, Any]:
        node_id = str(node_id or "").strip()
        if not node_id:
            raise ValueError("node_id required")
        if allowed_workspaces is not None and not self.filter_scoped_nodes(
            [{"id": node_id}],
            allowed_workspaces,
            include_legacy_global=include_legacy_global,
        ):
            raise ValueError(f"graph node not found: {node_id}")
        depth = max(0, min(int(depth or 1), 4))
        limit = max(1, min(int(limit or 100), 500))
        nt, et = self._read_tables()
        visited = {node_id}
        frontier = {node_id}
        edges_by_id: Dict[str, Dict[str, Any]] = {}
        with self._connect() as conn:
            for _ in range(depth):
                if not frontier or len(visited) >= limit:
                    break
                placeholders = ",".join("?" * len(frontier))
                rows = conn.execute(
                    f"""
                        SELECT id, from_node, to_node, type, weight, metadata_json
                        FROM {et}
                        WHERE from_node IN ({placeholders}) OR to_node IN ({placeholders})
                        ORDER BY weight DESC, id ASC
                        LIMIT ?
                        """,
                    (*frontier, *frontier, limit * 3),
                ).fetchall()
                next_frontier = set()
                for row in rows:
                    edges_by_id[row["id"]] = {
                        "id": row["id"],
                        "from": row["from_node"],
                        "to": row["to_node"],
                        "type": row["type"],
                        "weight": row["weight"],
                        "metadata": _safe_loads(row["metadata_json"]),
                    }
                    for candidate in (row["from_node"], row["to_node"]):
                        if candidate not in visited and len(visited) < limit:
                            visited.add(candidate)
                            next_frontier.add(candidate)
                frontier = next_frontier
            placeholders = ",".join("?" * len(visited))
            node_rows = conn.execute(
                f"""
                    SELECT id, type, title, summary, metadata_json, updated_at
                    FROM {nt}
                    WHERE id IN ({placeholders})
                    ORDER BY updated_at DESC, id ASC
                    """,
                list(visited),
            ).fetchall()
        nodes = [
                {
                    "id": row["id"],
                    "type": row["type"],
                    "title": row["title"],
                    "summary": row["summary"],
                    "metadata": _safe_loads(row["metadata_json"]),
                    "updated_at": row["updated_at"],
                }
                for row in node_rows
            ]
        edges = list(edges_by_id.values())
        if allowed_workspaces is not None:
            nodes = self.filter_scoped_nodes(
                nodes,
                allowed_workspaces,
                include_legacy_global=include_legacy_global,
            )
            kept = {node.get("id") for node in nodes}
            edges = [edge for edge in edges if edge.get("from") in kept and edge.get("to") in kept]
        return {"root": node_id, "depth": depth, "nodes": nodes, "edges": edges}

    def stats(
        self,
        *,
        allowed_workspaces=None,
        include_legacy_global: bool = False,
    ) -> Dict[str, Any]:
        """Store statistics, optionally restricted to a caller's workspaces.

        ``allowed_workspaces=None`` keeps the historical whole-store counts
        (single-user / no-auth mode). When a scope is given, the node and edge
        histograms are counted through the authoritative ``nodes_v2``
        projection, so a member of one organization workspace cannot read
        another's volume off this endpoint. An edge counts only when *both*
        endpoints are visible — the same rule ``graph()`` and ``neighbors()``
        already apply to the rows they return.
        """
        nt, et = self._read_tables()
        scope_sql, scope_params = self._workspace_scope_sql(
            allowed_workspaces, include_legacy_global
        )
        with self._connect() as conn:
            if scope_sql is None:
                node_counts = {
                    row["type"]: row["count"]
                    for row in conn.execute(
                        f"SELECT type, COUNT(*) AS count FROM {nt} GROUP BY type"
                    )
                }
                edge_counts = {
                    row["type"]: row["count"]
                    for row in conn.execute(
                        f"SELECT type, COUNT(*) AS count FROM {et} GROUP BY type"
                    )
                }
                local_sources = conn.execute(
                    "SELECT COUNT(*) AS c FROM knowledge_sources"
                ).fetchone()["c"]
                local_file_status = {
                    row["status"]: row["count"]
                    for row in conn.execute(
                        "SELECT status, COUNT(*) AS count FROM local_file_index GROUP BY status"
                    )
                }
            else:
                visible = f"SELECT id FROM nodes_v2 WHERE {scope_sql}"
                node_counts = {
                    row["type"]: row["count"]
                    for row in conn.execute(
                        f"SELECT type, COUNT(*) AS count FROM {nt} "
                        f"WHERE id IN ({visible}) GROUP BY type",
                        scope_params,
                    )
                }
                edge_counts = {
                    row["type"]: row["count"]
                    for row in conn.execute(
                        f"SELECT type, COUNT(*) AS count FROM {et} "
                        f"WHERE from_node IN ({visible}) AND to_node IN ({visible}) "
                        f"GROUP BY type",
                        scope_params + scope_params,
                    )
                }
                # Local sources and the file index are machine-local ingestion
                # bookkeeping with no workspace column. They are not another
                # tenant's content, but they are also not this caller's scope,
                # so a scoped read reports none rather than guessing.
                local_sources = 0
                local_file_status = {}
        v2 = None
        if KGStoreV2 is not None:
            try:
                v2 = (
                    KGStoreV2(self.db_path).stats()
                    if scope_sql is None
                    else self._scoped_v2_stats(scope_sql, scope_params)
                )
            except Exception as e:
                v2 = {"available": False, "error": str(e)}
        return {
            "db_path": str(self.db_path),
            "schema_version": GRAPH_SCHEMA_VERSION,
            "v2_schema_available": KGStoreV2 is not None,
            "nodes": node_counts,
            "edges": edge_counts,
            "local_sources": local_sources,
            "local_file_status": local_file_status,
            "v2": v2,
        }

    def _scoped_v2_stats(self, scope_sql: str, scope_params: List[Any]) -> Dict[str, Any]:
        """``KGStoreV2.stats()`` restricted to a caller's workspaces.

        Starts from the real payload and overwrites only the counts, so the
        key set stays whatever ``KGStoreV2`` defines — ``/knowledge-graph/schema``
        returns this sub-object verbatim, making its shape part of the API
        contract rather than something to re-derive here.
        """
        payload: Dict[str, Any] = dict(KGStoreV2(self.db_path).stats())
        visible = f"SELECT id FROM nodes_v2 WHERE {scope_sql}"
        with self._connect() as conn:
            by_node_type = {
                row["type"]: row["c"]
                for row in conn.execute(
                    f"SELECT type, COUNT(*) AS c FROM nodes_v2 "
                    f"WHERE {scope_sql} GROUP BY type",
                    scope_params,
                ).fetchall()
            }
            by_edge_type = {
                row["type"]: row["c"]
                for row in conn.execute(
                    f"SELECT type, COUNT(*) AS c FROM edges_v2 "
                    f"WHERE source IN ({visible}) AND target IN ({visible}) "
                    f"GROUP BY type",
                    scope_params + scope_params,
                ).fetchall()
            }
        payload.update(
            {
                "nodes": sum(by_node_type.values()),
                "edges": sum(by_edge_type.values()),
                "by_node_type": by_node_type,
                "by_edge_type": by_edge_type,
            }
        )
        return payload
