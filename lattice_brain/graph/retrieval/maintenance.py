"""Destructive graph maintenance: drop one conversation, or everything.

Both methods gate their ``nodes_v2`` work on ``KGStoreV2``. That name is
star-imported from ``_kg_common`` into **this** module's globals, so the
patch target for "pretend the v2 projection is unavailable" is
``lattice_brain.graph.retrieval.maintenance.KGStoreV2``.
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


class _MaintenanceMixin(_Core):
    """Conversation/graph deletion. Composed into the public mixin."""

    def delete_conversation(self, conversation_id: str) -> Dict[str, Any]:
        conversation_id = str(conversation_id or "").strip()
        if not conversation_id:
            return {"status": "skipped", "removed_nodes": 0}
        conv_id = f"conversation:{_slug(conversation_id)}"
        with self._connect() as conn:
            # Edge rows may carry the legacy lowercase label (pre-v4) or the
            # canonical EdgeType value (v4 write door) — match both.
            direct_ids = [
                row["to_node"]
                for row in conn.execute(
                    "SELECT to_node FROM edges WHERE from_node=? AND type IN ('contains', 'CONTAINS')",
                    (conv_id,),
                )
            ]
            remove_ids = set(direct_ids)
            child_types = [
                "has_chunk",
                "implies",
                "contains_signal",
                "has_page",
                "has_slide",
                "has_sheet",
                "contains_image",
            ]
            child_types += [t.upper() for t in child_types]
            placeholders = ",".join("?" for _ in child_types)
            for source_id in list(direct_ids):
                for row in conn.execute(
                    f"SELECT to_node FROM edges WHERE from_node=? AND type IN ({placeholders})",
                    (source_id, *child_types),
                ):
                    remove_ids.add(row["to_node"])
            remove_ids.add(conv_id)
            for node_id in remove_ids:
                conn.execute("DELETE FROM nodes WHERE id=?", (node_id,))
                if KGStoreV2 is not None:
                    conn.execute(
                        "DELETE FROM nodes_v2 WHERE id=?", (node_id,)
                    )  # edges_v2 cascade
            conn.execute(
                """
                    DELETE FROM nodes
                    WHERE type='Topic'
                      AND id NOT IN (SELECT to_node FROM edges)
                      AND id NOT IN (SELECT from_node FROM edges)
                    """
            )
            if KGStoreV2 is not None:
                conn.execute(
                    """
                        DELETE FROM nodes_v2
                        WHERE legacy_type='Topic'
                          AND id NOT IN (SELECT target FROM edges_v2)
                          AND id NOT IN (SELECT source FROM edges_v2)
                        """
                )
        return {
            "status": "ok",
            "conversation_id": conversation_id,
            "removed_nodes": len(remove_ids),
        }

    def clear_all(self) -> Dict[str, Any]:
        with self._connect() as conn:
            counts = {
                "nodes": conn.execute("SELECT COUNT(*) AS c FROM nodes").fetchone()[
                    "c"
                ],
                "edges": conn.execute("SELECT COUNT(*) AS c FROM edges").fetchone()[
                    "c"
                ],
                "chunks": conn.execute("SELECT COUNT(*) AS c FROM chunks").fetchone()[
                    "c"
                ],
                "knowledge_sources": conn.execute(
                    "SELECT COUNT(*) AS c FROM knowledge_sources"
                ).fetchone()["c"],
                "local_file_index": conn.execute(
                    "SELECT COUNT(*) AS c FROM local_file_index"
                ).fetchone()["c"],
            }
            conn.execute("DELETE FROM local_file_index")
            conn.execute("DELETE FROM knowledge_sources")
            conn.execute("DELETE FROM chunks")
            conn.execute("DELETE FROM edges")
            conn.execute("DELETE FROM nodes")
            if KGStoreV2 is not None:
                conn.execute("DELETE FROM edges_v2")
                conn.execute("DELETE FROM nodes_v2")
        if self.blob_dir.exists():
            shutil.rmtree(self.blob_dir, ignore_errors=True)
            self.blob_dir.mkdir(parents=True, exist_ok=True)
        return {"status": "ok", "removed": counts}
