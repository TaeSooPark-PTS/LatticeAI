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



class KnowledgeGraphProvenanceMixin(_Core):
    def record_provenance(
        self,
        *,
        node_id: str,
        source_type: str,
        pipeline: str = "unified-ingestion",
        source_uri: Optional[str] = None,
        content_hash: Optional[str] = None,
        title: Optional[str] = None,
        owner: Optional[str] = None,
        workspace_id: Optional[str] = None,
        captured_at: Optional[str] = None,
        modified_at: Optional[str] = None,
        embedded: bool = False,
        linked: bool = False,
        duplicate: bool = False,
        agent_used: Optional[str] = None,
        chunk_count: int = 0,
        permissions: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Append a provenance record for an ingested node (audit trail)."""
        now = _now()
        prov_basis = f"{node_id}|{content_hash or ''}|{now}"
        prov_id = f"prov:{_sha256_text(prov_basis)[:24]}"
        with self._connect() as conn:
            conn.execute(
                """
                    INSERT OR REPLACE INTO ingestion_provenance(
                      id, node_id, source_type, source_uri, content_hash, title, pipeline,
                      owner, workspace_id, captured_at, modified_at, embedded, linked,
                      duplicate, agent_used, chunk_count, permissions_json, metadata_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                (
                    prov_id,
                    node_id,
                    source_type,
                    source_uri,
                    content_hash,
                    title,
                    pipeline,
                    owner,
                    workspace_id,
                    captured_at,
                    modified_at,
                    1 if embedded else 0,
                    1 if linked else 0,
                    1 if duplicate else 0,
                    agent_used,
                    int(chunk_count or 0),
                    _json(permissions or {}),
                    _json(metadata or {}),
                    now,
                ),
            )
        return {"id": prov_id, "node_id": node_id, "created_at": now}

    @staticmethod
    def _provenance_row(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "node_id": row["node_id"],
            "source_type": row["source_type"],
            "source_uri": row["source_uri"],
            "content_hash": row["content_hash"],
            "title": row["title"],
            "pipeline": row["pipeline"],
            "owner": row["owner"],
            "workspace_id": row["workspace_id"],
            "captured_at": row["captured_at"],
            "modified_at": row["modified_at"],
            "embedded": bool(row["embedded"]),
            "linked": bool(row["linked"]),
            "duplicate": bool(row["duplicate"]),
            "agent_used": row["agent_used"],
            "chunk_count": row["chunk_count"],
            "permissions": _safe_loads(row["permissions_json"]),
            "metadata": _safe_loads(row["metadata_json"]),
            "created_at": row["created_at"],
        }

    def get_provenance(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Return the most recent provenance record for a node, or None."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM ingestion_provenance WHERE node_id = ? "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (node_id,),
            ).fetchone()
            return self._provenance_row(row) if row else None

    def list_provenance(
        self, *, limit: int = 100, source_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """Recent provenance records (newest first), optionally by source_type."""
        limit = max(1, min(int(limit or 100), 1000))
        with self._connect() as conn:
            if source_type:
                rows = conn.execute(
                    "SELECT * FROM ingestion_provenance WHERE source_type = ? "
                    "ORDER BY created_at DESC, rowid DESC LIMIT ?",
                    (source_type, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM ingestion_provenance "
                    "ORDER BY created_at DESC, rowid DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return {
                "items": [self._provenance_row(r) for r in rows],
                "count": len(rows),
            }

    def provenance_coverage(self) -> Dict[str, Any]:
        """How much of the brain is explainable: nodes with vs without
        provenance, per node type — the honesty metric for 'every source goes
        through the pipeline'. Pre-v4 nodes ingested before provenance existed
        legitimately count as uncovered."""
        nt, _ = self._read_tables()
        with self._connect() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM {nt}").fetchone()[0]
            covered = conn.execute(
                f"SELECT COUNT(*) FROM {nt} WHERE id IN (SELECT DISTINCT node_id FROM ingestion_provenance)"
            ).fetchone()[0]
            uncovered_by_type = {
                row["type"]: row["c"]
                for row in conn.execute(
                    f"""
                        SELECT type, COUNT(*) AS c FROM {nt}
                        WHERE id NOT IN (SELECT DISTINCT node_id FROM ingestion_provenance)
                        GROUP BY type ORDER BY c DESC LIMIT 20
                        """
                ).fetchall()
            }
            by_source = {
                row["source_type"]: row["c"]
                for row in conn.execute(
                    "SELECT source_type, COUNT(*) AS c FROM ingestion_provenance GROUP BY source_type"
                ).fetchall()
            }
        return {
            "total_nodes": total,
            "nodes_with_provenance": covered,
            "coverage_ratio": round(covered / total, 4) if total else None,
            "uncovered_by_type": uncovered_by_type,
            "provenance_by_source_type": by_source,
        }

    def provenance_stats(self) -> Dict[str, Any]:
        """Aggregate provenance counts for the Knowledge Graph status surface."""
        with self._connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) AS c FROM ingestion_provenance"
            ).fetchone()["c"]
            by_source = {
                r["source_type"]: r["c"]
                for r in conn.execute(
                    "SELECT source_type, COUNT(*) AS c FROM ingestion_provenance GROUP BY source_type"
                ).fetchall()
            }
            embedded = conn.execute(
                "SELECT COUNT(*) AS c FROM ingestion_provenance WHERE embedded = 1"
            ).fetchone()["c"]
            duplicates = conn.execute(
                "SELECT COUNT(*) AS c FROM ingestion_provenance WHERE duplicate = 1"
            ).fetchone()["c"]
            last = conn.execute(
                "SELECT created_at FROM ingestion_provenance ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return {
            "total": total,
            "by_source_type": by_source,
            "embedded": embedded,
            "duplicates": duplicates,
            "last_ingested_at": last["created_at"] if last else None,
        }

    def schema_versions(self) -> Dict[str, Any]:
        """Versions an exporter stamps and an importer validates against."""
        try:
            from .schema import EMBED_DIM as _EMBED_DIM
            from .schema import KG_SCHEMA_V2_VERSION as _V2
        except Exception:  # pragma: no cover - kg_schema always importable in practice
            _EMBED_DIM, _V2 = 1024, 2
        return {
            "graph_schema_version": GRAPH_SCHEMA_VERSION,
            "db_format_version": _KG_DB_FORMAT_VERSION,
            "kg_v2_schema_version": _V2,
            "projection_version": _PROJECTION_VERSION,
            "embed_dim": _EMBED_DIM,
        }

    def export_graph_data(
        self,
        *,
        workspace_id: Optional[str] = None,
        include_legacy_global: bool = False,
    ) -> Dict[str, Any]:
        """Raw, lossless logical export of the graph (nodes/edges/chunks/sources/
        provenance). Vector embeddings are intentionally omitted — they are
        re-derived on import — so the artifact stays portable and small. Use
        :meth:`backup_database` for a faithful binary copy incl. embeddings.

        ``workspace_id`` REALLY filters (v4): the artifact contains only nodes
        scoped to that workspace, with edges/chunks/provenance restricted to the
        surviving nodes. Legacy-global rows require the explicit
        ``include_legacy_global=True`` migration/compatibility opt-in. Pre-v4
        this parameter was stamped into the header while the data exported
        everything — a header that lied.
        """
        with self._connect() as conn:

            def rows(table: str):
                return [
                    dict(r) for r in conn.execute(f"SELECT * FROM {table}").fetchall()
                ]

            if workspace_id:
                scope_sql = "workspace_id = ?"
                if include_legacy_global:
                    scope_sql += " OR workspace_id IS NULL"
                keep_ids = {
                    row["id"]
                    for row in conn.execute(
                        f"SELECT id FROM nodes_v2 WHERE {scope_sql}",
                        (workspace_id,),
                    ).fetchall()
                }
                nodes = [n for n in rows("nodes") if n["id"] in keep_ids]
                edges = [
                    e
                    for e in rows("edges")
                    if e["from_node"] in keep_ids and e["to_node"] in keep_ids
                ]
                chunks = [c for c in rows("chunks") if c["source_node"] in keep_ids]
                provenance = [
                    p for p in rows("ingestion_provenance") if p["node_id"] in keep_ids
                ]
                data = {
                    "nodes": nodes,
                    "edges": edges,
                    "chunks": chunks,
                    "knowledge_sources": rows("knowledge_sources"),
                    "provenance": provenance,
                }
            else:
                data = {
                    "nodes": rows("nodes"),
                    "edges": rows("edges"),
                    "chunks": rows("chunks"),
                    "knowledge_sources": rows("knowledge_sources"),
                    "provenance": rows("ingestion_provenance"),
                }
        data["counts"] = {k: len(v) for k, v in data.items()}
        return data

    def import_graph_data(
        self, data: Dict[str, Any], *, mode: str = "merge", dry_run: bool = False
    ) -> Dict[str, Any]:
        """Import a logical export back into the store.

        ``mode='merge'`` upserts on top of existing data (id collisions update);
        ``mode='replace'`` clears the graph first. ``dry_run=True`` reports the
        plan without writing. Refuses artifacts from a NEWER graph schema than
        this build.
        """
        nodes = data.get("nodes") or []
        edges = data.get("edges") or []
        chunks = data.get("chunks") or []
        sources = data.get("knowledge_sources") or []
        provenance = data.get("provenance") or []

        header = data.get("header") or {}
        incoming_schema = header.get("graph_schema_version")
        if isinstance(incoming_schema, int) and incoming_schema > GRAPH_SCHEMA_VERSION:
            raise ValueError(
                f"Artifact graph_schema_version {incoming_schema} is newer than this "
                f"build ({GRAPH_SCHEMA_VERSION}); refusing to import."
            )

        plan = {
            "mode": mode,
            "nodes": len(nodes),
            "edges": len(edges),
            "chunks": len(chunks),
            "knowledge_sources": len(sources),
            "provenance": len(provenance),
        }
        if dry_run:
            plan["dry_run"] = True
            return plan

        with self._connect() as conn:
            if mode == "replace":
                # Keep replacement imports transactional. The old clear_all()
                # path committed before the import started, so a malformed
                # artifact could leave a cleared or partially rebuilt graph.
                # These deletes roll back with the rest of the import.
                for table in (
                    "local_file_index",
                    "knowledge_sources",
                    "chunks",
                    "edges",
                    "nodes",
                    "vector_embeddings",
                ):
                    conn.execute(f"DELETE FROM {table}")
                if KGStoreV2 is not None:
                    conn.execute("DELETE FROM edges_v2")
                    conn.execute("DELETE FROM nodes_v2")
            for n in nodes:
                self._upsert_node(
                    conn,
                    n["id"],
                    n["type"],
                    n.get("title") or "",
                    summary=n.get("summary") or "",
                    metadata=_safe_loads(n.get("metadata_json")),
                    raw=_safe_loads(n.get("raw_json")),
                )
            for c in chunks:
                self._upsert_chunk(
                    conn,
                    chunk_id=c["id"],
                    source_node=c["source_node"],
                    text=c.get("text") or "",
                    metadata=_safe_loads(c.get("metadata_json")),
                )
            for e in edges:
                e_meta = _safe_loads(e.get("metadata_json")) or {}
                leg_label = e_meta.get("legacy_label")
                if not leg_label:
                    orig = e.get("type") or ""
                    if orig:
                        # preserve whatever label came from export (legacy or canon)
                        leg_label = orig
                self._upsert_edge(
                    conn,
                    e["from_node"],
                    e["to_node"],
                    e["type"],
                    weight=float(e.get("weight") or 1.0),
                    metadata=e_meta,
                    legacy_label=leg_label,
                )
            for s in sources:
                conn.execute(
                    """
                        INSERT OR REPLACE INTO knowledge_sources(
                          id, root_path, os_type, drive_id, label, status, include_ocr,
                          watch_enabled, consent_json, created_at, updated_at, last_scanned_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                    (
                        s["id"],
                        s["root_path"],
                        s["os_type"],
                        s.get("drive_id"),
                        s.get("label"),
                        s.get("status") or "active",
                        int(s.get("include_ocr") or 0),
                        int(s.get("watch_enabled") or 0),
                        s.get("consent_json") or "{}",
                        s.get("created_at") or _now(),
                        s.get("updated_at") or _now(),
                        s.get("last_scanned_at"),
                    ),
                )
            for p in provenance:
                conn.execute(
                    """
                        INSERT OR REPLACE INTO ingestion_provenance(
                          id, node_id, source_type, source_uri, content_hash, title, pipeline,
                          owner, workspace_id, captured_at, modified_at, embedded, linked,
                          duplicate, agent_used, chunk_count, permissions_json, metadata_json, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                    (
                        p["id"],
                        p["node_id"],
                        p["source_type"],
                        p.get("source_uri"),
                        p.get("content_hash"),
                        p.get("title"),
                        p.get("pipeline") or "import",
                        p.get("owner"),
                        p.get("workspace_id"),
                        p.get("captured_at"),
                        p.get("modified_at"),
                        int(p.get("embedded") or 0),
                        int(p.get("linked") or 0),
                        int(p.get("duplicate") or 0),
                        p.get("agent_used"),
                        int(p.get("chunk_count") or 0),
                        p.get("permissions_json") or "{}",
                        p.get("metadata_json") or "{}",
                        p.get("created_at") or _now(),
                    ),
                )
        plan["imported"] = True
        return plan

    def backup_database(self, dest_path) -> Path:
        """Write a clean, standalone snapshot of the live DB to ``dest_path``.

        Uses ``VACUUM INTO`` (after a full WAL checkpoint) so the snapshot is a
        defragmented, rollback-journal-mode database with no companion -wal/-shm
        — which restores cleanly by a plain file copy. Captures all data incl.
        the vector_embeddings BLOBs.
        """
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest.unlink()  # VACUUM INTO requires the target to not exist
        # Raw connection, not ``_connect()``: VACUUM cannot run inside a
        # transaction, and ``_connect()`` wraps its block in one.
        conn = self.storage_engine.connect()
        try:
            conn.execute("PRAGMA wal_checkpoint(FULL)")
            conn.execute("VACUUM INTO ?", (str(dest),))
        finally:
            conn.close()
        return dest
