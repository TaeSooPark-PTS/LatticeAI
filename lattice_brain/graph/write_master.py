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



class KnowledgeGraphWriteMixin(_Core):
    def _upsert_node(
        self,
        conn: sqlite3.Connection,
        node_id: str,
        node_type: str,
        title: str,
        summary: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        raw: Optional[Dict[str, Any]] = None,
        owner: Optional[str] = None,
        workspace_id: Optional[str] = None,
        visibility: Optional[str] = None,
    ) -> str:
        now = _now()
        # v4 write-mastering: nodes_v2 is authoritative; the legacy nodes
        # table is maintained as the compatibility projection.
        title_s = title[:240]
        summary_s = summary[:1000]
        meta_json = _json(metadata)
        self._v2_project_node(
            conn,
            node_id,
            node_type,
            title_s,
            summary_s,
            meta_json,
            created_at=now,
            updated_at=now,
            owner=owner,
            workspace_id=workspace_id,
            visibility=visibility,
            strict=True,
        )
        conn.execute(
            """
                INSERT INTO nodes(id, type, title, summary, metadata_json, raw_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  title=excluded.title,
                  summary=excluded.summary,
                  metadata_json=excluded.metadata_json,
                  raw_json=excluded.raw_json,
                  updated_at=excluded.updated_at
                """,
            (node_id, node_type, title_s, summary_s, meta_json, _json(raw), now, now),
        )
        if node_type != "Chunk":
            self._upsert_vector_item(
                conn,
                item_id=node_id,
                item_type="node",
                source_node=node_id,
                text=self._vector_text_for_node(
                    title=title_s, summary=summary_s, metadata=metadata
                ),
                metadata={"node_type": node_type, **(metadata or {})},
            )
        return node_id

    def _upsert_edge(
        self,
        conn: sqlite3.Connection,
        from_node: str,
        to_node: str,
        edge_type: str,
        weight: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None,
        *,
        legacy_label: Optional[str] = None,
    ) -> str:
        # v4 write door: every new edge stores the canonical EdgeType value —
        # free-string types (e.g. '포함함', '언급함') are normalized here, so no
        # caller can mint new legacy taxonomy. The original label survives in
        # metadata.legacy_label for traceability.
        #
        # legacy_type in edges_v2:
        #   - normal write door: synonyms always dedupe to ONE row with legacy_type=''
        #   - import (passes legacy_label=) : preserves distinct legacy labels as
        #     separate v2 rows (lossless collision for old artifacts / backfill)
        passed_for_legacy = legacy_label or edge_type
        if EdgeType is not None:
            canonical = EdgeType.from_legacy(edge_type).value
            if canonical != edge_type or (legacy_label and legacy_label != canonical):
                metadata = dict(metadata or {})
                ll = legacy_label or edge_type
                if ll != canonical:
                    metadata.setdefault("legacy_label", ll)
            edge_type = canonical
        edge_id = f"edge:{_sha256_text(f'{from_node}|{edge_type}|{to_node}')[:24]}"
        now = _now()
        meta_json = _json(metadata)  # canonical string shared with the projection
        v2_legacy = ""
        if passed_for_legacy and passed_for_legacy != edge_type:
            if legacy_label is not None:
                # explicit legacy_label from import path forces distinct legacy_type
                # rows in v2 for collision preservation
                v2_legacy = passed_for_legacy
            else:
                # normal write-door dedupes even for synonym labels (legacy_type='')
                v2_legacy = ""
        # v2 may use distinct eid when preserving legacy collisions (different from
        # the canon-based edge_id used for legacy edges table which always dedupes)
        v2_eid = (
            f"edge:{_sha256_text(f'{from_node}|{edge_type}|{to_node}|{v2_legacy}')[:24]}"
            if v2_legacy
            else edge_id
        )
        self._v2_project_edge(
            conn,
            from_node,
            to_node,
            edge_type,
            float(weight),
            meta_json,
            edge_id=v2_eid,
            created_at=now,
            strict=True,
            legacy_type=v2_legacy,
        )
        conn.execute(
            """
                INSERT INTO edges(id, from_node, to_node, type, weight, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(from_node, to_node, type) DO UPDATE SET
                  weight=max(edges.weight, excluded.weight),
                  metadata_json=excluded.metadata_json
                """,
            (edge_id, from_node, to_node, edge_type, float(weight), meta_json, now),
        )
        return edge_id

    def _vector_text_for_node(
        self,
        *,
        title: str,
        summary: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        metadata = metadata or {}
        meta_parts = []
        for key in (
            "filename",
            "relative_path",
            "file_path",
            "conversation_id",
            "source",
            "category",
            "ext",
            "role",
        ):
            value = metadata.get(key)
            if value:
                meta_parts.append(str(value))
        return _clean_text(
            "\n".join([str(title or ""), str(summary or ""), " ".join(meta_parts)])
        )

    def _upsert_vector_item(
        self,
        conn: sqlite3.Connection,
        *,
        item_id: str,
        item_type: str,
        source_node: str,
        text: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        text = _clean_text(text)
        if len(text) < 2:
            conn.execute("DELETE FROM vector_embeddings WHERE item_id=?", (item_id,))
            return False
        text_hash = _sha256_text(text)
        existing = conn.execute(
            """
                SELECT text_hash, embedding_dim, embedding_model
                FROM vector_embeddings
                WHERE item_id=?
                """,
            (item_id,),
        ).fetchone()
        if (
            existing
            and existing["text_hash"] == text_hash
            and existing["embedding_dim"] == self._embedding_model.dim
            and existing["embedding_model"] == self._embedding_model.model_id
        ):
            return False
        embedding = self._embedding_model.encode(
            self._embedding_model.embed(text[:50_000])
        )
        conn.execute(
            """
                INSERT INTO vector_embeddings(
                  item_id, item_type, source_node, text_hash, embedding,
                  embedding_dim, embedding_model, metadata_json, indexed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_id) DO UPDATE SET
                  item_type=excluded.item_type,
                  source_node=excluded.source_node,
                  text_hash=excluded.text_hash,
                  embedding=excluded.embedding,
                  embedding_dim=excluded.embedding_dim,
                  embedding_model=excluded.embedding_model,
                  metadata_json=excluded.metadata_json,
                  indexed_at=excluded.indexed_at
                """,
            (
                item_id,
                item_type,
                source_node,
                text_hash,
                embedding,
                self._embedding_model.dim,
                self._embedding_model.model_id,
                _json(metadata),
                _now(),
            ),
        )
        return True

    def _upsert_chunk(
        self,
        conn: sqlite3.Connection,
        *,
        chunk_id: str,
        source_node: str,
        text: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        metadata = metadata or {}
        conn.execute(
            "INSERT OR REPLACE INTO chunks(id, source_node, text, metadata_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (chunk_id, source_node, text, _json(metadata), _now()),
        )
        self._upsert_vector_item(
            conn,
            item_id=chunk_id,
            item_type="chunk",
            source_node=chunk_id,
            text=text,
            metadata={**metadata, "parent_source_node": source_node},
        )

    def set_node_sensitivity(
        self,
        node_id: str,
        *,
        local_only: bool,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Mark (or unmark) one node as never-leaving.

        The cloud filter has always looked for this flag; until 10.2.0 nothing
        could set it, so the guard was unreachable. This is the user-driven
        half — ingestion stamps secret-bearing paths automatically, and this
        covers everything a path cannot tell you, like a note whose *content*
        is private.

        Unmarking is allowed and audited by the caller: a user who flagged
        something by mistake must be able to undo it, but the reason is cleared
        with the flag so a stale justification cannot linger.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT metadata_json FROM nodes WHERE id=?", (node_id,)
            ).fetchone()
            if row is None:
                return {"ok": False, "node_id": node_id, "reason": "node not found"}
            try:
                metadata = json.loads(row[0] or "{}")
            except Exception:
                metadata = {}
            if local_only:
                metadata["local_only"] = True
                metadata["local_only_reason"] = reason or "marked by the user"
            else:
                metadata.pop("local_only", None)
                metadata.pop("local_only_reason", None)
            conn.execute(
                "UPDATE nodes SET metadata_json=?, updated_at=? WHERE id=?",
                (json.dumps(metadata, ensure_ascii=False), _now(), node_id),
            )
        return {
            "ok": True,
            "node_id": node_id,
            "local_only": bool(local_only),
            "reason": metadata.get("local_only_reason"),
        }
