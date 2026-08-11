"""Removing a local file from the graph, and the scope checks around it.

Deletes a file's graph node, sweeps the concepts left orphaned by it, and
answers the two "is this row still good?" questions the scanner asks before
skipping work. Moved verbatim out of ``discovery_index.py`` (v11.3.0).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# ruff: noqa: F403,F405
from .._kg_common import *  # noqa: F403,F401

# The cross-mixin surface (`_connect`, `_upsert_node`, …) is declared in
# `_kg_contract.KnowledgeGraphCore`. It is a typing-only base: at runtime this
# is `object`, so the MRO of `KnowledgeGraphStore` is unchanged.
if TYPE_CHECKING:
    from .._kg_contract import KnowledgeGraphCore as _Core
else:
    _Core = object


class _LocalCleanupMixin(_Core):
    """Graph deletion + orphan sweep. Composed into the public mixin."""

    def _delete_local_file_graph(
        self, conn: sqlite3.Connection, file_node_id: Optional[str]
    ) -> None:
        if not file_node_id:
            return

        file_row = conn.execute(
            "SELECT metadata_json FROM nodes WHERE id=?",
            (file_node_id,),
        ).fetchone()
        source_id = None
        if file_row:
            source_id = _safe_loads(file_row["metadata_json"]).get("source_id")

        linked_rows = conn.execute(
            """
                SELECT n.id, n.type, n.metadata_json
                FROM edges e
                JOIN nodes n ON n.id=e.to_node
                WHERE e.from_node=?
                """,
            (file_node_id,),
        ).fetchall()
        owned_ids: set = set()
        auto_candidate_ids: set = set()
        for row in linked_rows:
            metadata = _safe_loads(row["metadata_json"])
            if (
                row["type"] in {"Chunk", "ImageText", "Section"}
                or metadata.get("source_node") == file_node_id
            ):
                owned_ids.add(row["id"])
            elif (
                metadata.get("auto_extracted")
                and metadata.get("source") == "local_folder"
            ):
                auto_candidate_ids.add(row["id"])

        conn.execute("DELETE FROM chunks WHERE source_node=?", (file_node_id,))
        conn.execute(
            "DELETE FROM edges WHERE from_node=? OR to_node=?",
            (file_node_id, file_node_id),
        )
        conn.execute("DELETE FROM nodes WHERE id=?", (file_node_id,))
        self._v2_delete_nodes(conn, [file_node_id])

        def delete_nodes(node_ids: set) -> None:
            if not node_ids:
                return
            placeholders = ",".join("?" * len(node_ids))
            params = list(node_ids)
            conn.execute(
                f"DELETE FROM chunks WHERE source_node IN ({placeholders})", params
            )
            conn.execute(
                f"DELETE FROM edges WHERE from_node IN ({placeholders}) OR to_node IN ({placeholders})",
                params * 2,
            )
            conn.execute(f"DELETE FROM nodes WHERE id IN ({placeholders})", params)
            self._v2_delete_nodes(conn, params)

        delete_nodes(owned_ids)

        removable_auto_ids: set = set()
        for node_id in auto_candidate_ids:
            remaining_edges = conn.execute(
                "SELECT from_node, to_node FROM edges WHERE from_node=? OR to_node=?",
                (node_id, node_id),
            ).fetchall()
            if all(
                (
                    row["from_node"] in auto_candidate_ids
                    and row["to_node"] in auto_candidate_ids
                )
                for row in remaining_edges
            ):
                removable_auto_ids.add(node_id)
        delete_nodes(removable_auto_ids)
        if source_id:
            self._cleanup_local_graph_orphans(conn, str(source_id))

    def _cleanup_local_graph_orphans(
        self, conn: sqlite3.Connection, source_id: str
    ) -> None:
        while True:
            folder_rows = conn.execute(
                "SELECT id, metadata_json FROM nodes WHERE type='Folder'"
            ).fetchall()
            leaf_ids = []
            for row in folder_rows:
                metadata = _safe_loads(row["metadata_json"])
                if metadata.get("source_id") != source_id:
                    continue
                has_children = conn.execute(
                    "SELECT 1 FROM edges WHERE from_node=? LIMIT 1",
                    (row["id"],),
                ).fetchone()
                if not has_children:
                    leaf_ids.append(row["id"])
            if not leaf_ids:
                break
            placeholders = ",".join("?" * len(leaf_ids))
            conn.execute(
                f"DELETE FROM edges WHERE from_node IN ({placeholders}) OR to_node IN ({placeholders})",
                leaf_ids * 2,
            )
            conn.execute(f"DELETE FROM nodes WHERE id IN ({placeholders})", leaf_ids)
            self._v2_delete_nodes(conn, leaf_ids)

        for node_type in ("Drive", "Computer"):
            rows = conn.execute(
                "SELECT id FROM nodes WHERE type=?", (node_type,)
            ).fetchall()
            removable = []
            for row in rows:
                has_children = conn.execute(
                    "SELECT 1 FROM edges WHERE from_node=? LIMIT 1",
                    (row["id"],),
                ).fetchone()
                if not has_children:
                    removable.append(row["id"])
            if removable:
                placeholders = ",".join("?" * len(removable))
                conn.execute(
                    f"DELETE FROM edges WHERE from_node IN ({placeholders}) OR to_node IN ({placeholders})",
                    removable * 2,
                )
                conn.execute(
                    f"DELETE FROM nodes WHERE id IN ({placeholders})", removable
                )
                self._v2_delete_nodes(conn, removable)

    def _local_file_index_has_extracted_text(self, row: sqlite3.Row) -> bool:
        metadata = _safe_loads(row["metadata_json"])
        parser = metadata.get("parser") if isinstance(metadata, dict) else {}
        if not isinstance(parser, dict):
            return False
        try:
            return int(parser.get("extracted_chars") or 0) > 0
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _node_matches_workspace(
        conn: sqlite3.Connection,
        node_id: Optional[str],
        workspace_id: Optional[str],
    ) -> bool:
        """Return true only when the projected node has the expected scope."""
        if not node_id:
            return False
        row = conn.execute(
            "SELECT workspace_id FROM nodes_v2 WHERE id=?",
            (node_id,),
        ).fetchone()
        return bool(row is not None and row["workspace_id"] == workspace_id)
