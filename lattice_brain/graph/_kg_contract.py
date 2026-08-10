"""The seam eleven graph mixins share.

``KnowledgeGraphStore`` is assembled from eleven mixins that freely call each
other's methods — ``ingest`` calls ``_upsert_node`` from ``write_master``,
``write_master`` calls ``_v2_project_node`` from ``projection``, everything
calls ``_connect`` from the store itself. That contract existed, but nowhere in
the code: a reader had to grep eleven files to learn what a mixin may assume,
and a type checker reported 229 ``attr-defined`` errors because from *its*
position each mixin is a bare class calling methods it does not have.

This module writes the contract down. It is **typing-only** — the mixins alias
it to ``object`` at runtime, so the MRO and every method resolution are
byte-for-byte what they were. What changes is that the shared surface is now 23
declared names in one file instead of an unwritten convention, and adding a
cross-mixin call without declaring it here is a type error.

Signatures mirror the real implementations; ``tests/unit/test_kg_contract.py``
asserts the concrete store still provides every member.
"""

from __future__ import annotations

import sqlite3
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


class KnowledgeGraphCore:
    """What any graph mixin may assume about ``self``.

    Never instantiated and never inherited at runtime — see the module
    docstring. Members are declared, not implemented, on purpose: the concrete
    implementation lives in whichever mixin owns it.
    """

    # ── State owned by KnowledgeGraphStore.__init__ ──────────────────────────
    db_path: Path
    blob_dir: Path
    storage_engine: Any
    _embedding_model: Any
    _read_from_v2: bool
    _v2_projection_available: bool

    # ── store.py: connection + read-table resolution ─────────────────────────
    def _connect(self) -> AbstractContextManager[sqlite3.Connection]:
        """Transactional connection that is *closed* when the block exits."""
        raise NotImplementedError

    def _read_tables(self) -> Tuple[str, ...]:
        """``(nodes_table, edges_table)`` — legacy tables or the v2 views."""
        raise NotImplementedError

    # ── write_master.py: the single write door ───────────────────────────────
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
        raise NotImplementedError

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
        raise NotImplementedError

    def _upsert_chunk(
        self,
        conn: sqlite3.Connection,
        *,
        chunk_id: str,
        source_node: str,
        text: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        raise NotImplementedError

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
        raise NotImplementedError

    def _vector_text_for_node(
        self,
        *,
        title: str,
        summary: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        raise NotImplementedError

    # ── projection.py: legacy ↔ v2 projection + keyword index ────────────────
    def _v2_project_node(
        self,
        conn: sqlite3.Connection,
        node_id: str,
        node_type: str,
        title: str,
        summary: Optional[str],
        metadata_json: Optional[str],
        *,
        created_at: Optional[str] = None,
        updated_at: Optional[str] = None,
        owner: Optional[str] = None,
        workspace_id: Optional[str] = None,
        visibility: Optional[str] = None,
        strict: bool = False,
    ) -> None:
        raise NotImplementedError

    def _v2_project_edge(
        self,
        conn: sqlite3.Connection,
        from_node: str,
        to_node: str,
        edge_type: str,
        weight: float,
        metadata_json: Optional[str],
        *,
        edge_id: Optional[str] = None,
        created_at: Optional[str] = None,
        strict: bool = False,
        legacy_type: Optional[str] = None,
    ) -> None:
        raise NotImplementedError

    def _v2_delete_nodes(self, conn: sqlite3.Connection, ids: Any) -> None:
        raise NotImplementedError

    def _v2_delete_edges_from(self, conn: sqlite3.Connection, node_id: str) -> None:
        raise NotImplementedError

    def _fts_match_ids(
        self, conn: sqlite3.Connection, query: str, limit: int
    ) -> List[str]:
        raise NotImplementedError

    # ── retrieval_vector.py: derived vector index ────────────────────────────
    def rebuild_vector_index(
        self,
        *,
        full: bool = False,
        include_nodes: bool = True,
        include_chunks: bool = True,
    ) -> Dict[str, Any]:
        """Re-derive the vector index; also records the embedder fingerprint."""
        raise NotImplementedError

    def vector_freshness(self) -> Dict[str, Any]:
        """``{status, pending_items, total_items, detail}`` — never raises."""
        raise NotImplementedError

    # ── retrieval_reads.py: 1-hop walk (hybrid candidate expansion) ──────────
    def neighbors(
        self,
        node_id: str,
        *,
        allowed_workspaces: Any = None,
        include_legacy_global: bool = False,
        as_of: Any = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    # ── retrieval_reads.py: workspace scoping ────────────────────────────────
    def filter_scoped_nodes(
        self,
        items: Any,
        allowed_workspaces: Any,
        *,
        id_key: str = "id",
        include_legacy_global: bool = False,
    ) -> Any:
        raise NotImplementedError

    # ── documents.py: structured-document ingestion ──────────────────────────
    def _document_structure(self, path: Path, ext: str) -> Dict[str, Any]:
        raise NotImplementedError

    def _ingest_structure_nodes(
        self,
        conn: sqlite3.Connection,
        file_id: str,
        filename: str,
        structure: Dict[str, Any],
        *,
        owner: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> None:
        raise NotImplementedError

    # ── discovery.py / discovery_index.py: local filesystem indexing ─────────
    def _iter_local_scan_entries(
        self, root: Path, *, max_files: int
    ) -> Iterable[Dict[str, Any]]:
        raise NotImplementedError

    def _local_file_decision(
        self, path: Path, root: Path, stat: Any
    ) -> Dict[str, Any]:
        raise NotImplementedError

    def _delete_local_file_graph(
        self, conn: sqlite3.Connection, file_node_id: Optional[str]
    ) -> None:
        raise NotImplementedError

    def _cleanup_local_graph_orphans(
        self, conn: sqlite3.Connection, source_id: str
    ) -> None:
        raise NotImplementedError


#: Every name a mixin may reach for through ``self``. Kept in sync by
#: ``tests/unit/test_kg_contract.py`` — the test derives it from the class, so
#: this constant cannot drift from the declarations above.
KG_CORE_MEMBERS: Sequence[str] = tuple(
    sorted(
        name
        for name in vars(KnowledgeGraphCore)
        if not name.startswith("__")
    )
    + sorted(getattr(KnowledgeGraphCore, "__annotations__", {}))
)


__all__ = ["KG_CORE_MEMBERS", "KnowledgeGraphCore"]
