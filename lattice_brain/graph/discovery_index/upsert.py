"""One local file → its rows and its graph nodes.

The computer/drive/folder hierarchy, the ``local_file_index`` row, and the
file node with its concepts and edges. ``_local_scoped_slug`` lives here
because both hierarchy and node upsert call it. Moved verbatim out of
``discovery_index.py`` (v11.3.0 decomposition).
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


def _local_scoped_slug(prefix: str, value: str, workspace_id: Optional[str]) -> str:
    """Preserve legacy IDs while isolating newly workspace-scoped nodes."""
    slug = _slug(value)
    if not workspace_id:
        return f"{prefix}:{slug}"
    scope = _sha256_text(str(workspace_id))[:12]
    return f"{prefix}:{scope}:{slug}"


class _LocalUpsertMixin(_Core):
    """Hierarchy + file node/index upserts. Composed into the public mixin."""

    def _ensure_local_hierarchy(
        self,
        conn: sqlite3.Connection,
        *,
        source_id: str,
        root: Path,
        file_path: Path,
        os_type: str,
        drive_id: str,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> str:
        computer_label = platform.node() or "내 컴퓨터"
        computer_id = _local_scoped_slug("computer", computer_label, workspace_id)
        drive_identity = f"{workspace_id}|{os_type}:{drive_id}" if workspace_id else f"{os_type}:{drive_id}"
        drive_node_id = f"drive:{_sha256_text(drive_identity)[:24]}"
        root_folder_id = f"folder:{_sha256_text(f'{source_id}:root')[:24]}"
        self._upsert_node(
            conn,
            computer_id,
            "Computer",
            computer_label,
            metadata={"os_type": os_type, "workspace_id": workspace_id},
            owner=user_email,
            workspace_id=workspace_id,
        )
        self._upsert_node(
            conn,
            drive_node_id,
            "Drive",
            drive_id,
            metadata={"os_type": os_type, "drive_id": drive_id, "workspace_id": workspace_id},
            owner=user_email,
            workspace_id=workspace_id,
        )
        stale_parents = conn.execute(
            """
                SELECT e.from_node
                FROM edges e
                JOIN nodes n ON n.id=e.from_node
                WHERE e.to_node=? AND n.type='Drive' AND e.from_node<>?
                """,
            (root_folder_id, drive_node_id),
        ).fetchall()
        for row in stale_parents:
            conn.execute(
                "DELETE FROM edges WHERE from_node=? AND to_node=?",
                (row["from_node"], root_folder_id),
            )
            conn.execute(
                "DELETE FROM edges_v2 WHERE source=? AND target=?",
                (row["from_node"], root_folder_id),
            )
        self._upsert_edge(
            conn,
            computer_id,
            drive_node_id,
            "포함함",
            metadata={"source": "local_scan", "workspace_id": workspace_id},
        )
        self._upsert_node(
            conn,
            root_folder_id,
            "Folder",
            root.name or str(root),
            summary=str(root),
            metadata={"source_id": source_id, "path": str(root), "root": True, "workspace_id": workspace_id},
            owner=user_email,
            workspace_id=workspace_id,
        )
        self._upsert_edge(
            conn,
            drive_node_id,
            root_folder_id,
            "포함함",
            metadata={"source": "local_scan", "workspace_id": workspace_id},
        )

        try:
            relative_parent = file_path.parent.relative_to(root)
        except ValueError:
            relative_parent = Path()
        parent_id = root_folder_id
        current_path = root
        for part in relative_parent.parts:
            current_path = current_path / part
            folder_id = (
                f"folder:{_sha256_text(f'{source_id}:{current_path.as_posix()}')[:24]}"
            )
            self._upsert_node(
                conn,
                folder_id,
                "Folder",
                part,
                summary=str(current_path),
                metadata={
                    "source_id": source_id,
                    "path": str(current_path),
                    "root": False,
                    "workspace_id": workspace_id,
                },
                owner=user_email,
                workspace_id=workspace_id,
            )
            self._upsert_edge(
                conn,
                parent_id,
                folder_id,
                "포함함",
                metadata={"source": "local_scan", "workspace_id": workspace_id},
            )
            parent_id = folder_id
        return parent_id

    def _upsert_local_file_index(
        self,
        conn: sqlite3.Connection,
        *,
        source_id: str,
        root: Path,
        file_path: Path,
        stat: Optional[os.stat_result],
        os_type: str,
        drive_id: str,
        status: str,
        parser_type: str,
        sha256: Optional[str] = None,
        graph_node_id: Optional[str] = None,
        error_message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        try:
            relative_path = file_path.relative_to(root).as_posix()
        except ValueError:
            relative_path = file_path.name
        index_id = f"local-index:{_sha256_text(f'{source_id}:{relative_path}')[:24]}"
        now = _now()
        size = stat.st_size if stat else None
        modified_at = _safe_iso_from_stat_mtime(stat.st_mtime) if stat else ""
        conn.execute(
            """
                INSERT INTO local_file_index(
                  id, source_id, os_type, drive_id, root_path, file_path, relative_path,
                  file_name, extension, size_bytes, modified_at, sha256, last_scanned_at,
                  last_indexed_at, parser_type, status, error_message, graph_node_id,
                  deleted, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id, relative_path) DO UPDATE SET
                  os_type=excluded.os_type,
                  drive_id=excluded.drive_id,
                  root_path=excluded.root_path,
                  file_path=excluded.file_path,
                  file_name=excluded.file_name,
                  extension=excluded.extension,
                  size_bytes=excluded.size_bytes,
                  modified_at=excluded.modified_at,
                  sha256=excluded.sha256,
                  last_scanned_at=excluded.last_scanned_at,
                  last_indexed_at=excluded.last_indexed_at,
                  parser_type=excluded.parser_type,
                  status=excluded.status,
                  error_message=excluded.error_message,
                  graph_node_id=excluded.graph_node_id,
                  deleted=excluded.deleted,
                  metadata_json=excluded.metadata_json
                """,
            (
                index_id,
                source_id,
                os_type,
                drive_id,
                str(root),
                str(file_path),
                relative_path,
                file_path.name,
                file_path.suffix.lower(),
                size,
                modified_at,
                sha256,
                now,
                now if status == "indexed" else None,
                parser_type,
                status,
                error_message,
                graph_node_id,
                0 if status != "deleted" else 1,
                _json(metadata),
            ),
        )
        return index_id

    def _upsert_local_file_node(
        self,
        conn: sqlite3.Connection,
        *,
        source_id: str,
        root: Path,
        file_path: Path,
        stat: os.stat_result,
        os_type: str,
        drive_id: str,
        sha256: str,
        category: str,
        parser_type: str,
        text: str,
        parser_meta: Dict[str, Any],
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> str:
        text = _clean_text(text)
        if not text:
            raise ValueError("텍스트 추출 결과가 비어 있습니다.")
        try:
            relative_path = file_path.relative_to(root).as_posix()
        except ValueError:
            relative_path = file_path.name
        file_node_id = f"local-file:{_sha256_text(f'{source_id}:{relative_path}')[:24]}"
        parent_folder_id = self._ensure_local_hierarchy(
            conn,
            source_id=source_id,
            root=root,
            file_path=file_path,
            os_type=os_type,
            drive_id=drive_id,
            user_email=user_email,
            workspace_id=workspace_id,
        )
        linked_rows = conn.execute(
            """
                SELECT e.to_node AS id, n.type, n.metadata_json
                FROM edges e
                JOIN nodes n ON n.id=e.to_node
                WHERE e.from_node=?
                """,
            (file_node_id,),
        ).fetchall()
        child_ids = []
        auto_candidate_ids = set()
        for row in linked_rows:
            linked_metadata = _safe_loads(row["metadata_json"])
            if row["type"] in {"Chunk", "ImageText", "Section"} or linked_metadata.get("source_node") == file_node_id:
                child_ids.append(row["id"])
            elif linked_metadata.get("auto_extracted") and linked_metadata.get("source") == "local_folder":
                auto_candidate_ids.add(row["id"])
        conn.execute("DELETE FROM chunks WHERE source_node=?", (file_node_id,))
        if child_ids:
            placeholders = ",".join("?" * len(child_ids))
            conn.execute(f"DELETE FROM nodes WHERE id IN ({placeholders})", child_ids)
            self._v2_delete_nodes(conn, child_ids)
        conn.execute("DELETE FROM edges WHERE from_node=?", (file_node_id,))
        self._v2_delete_edges_from(conn, file_node_id)
        removable_auto_ids = set()
        for node_id in auto_candidate_ids:
            remaining_edges = conn.execute(
                "SELECT from_node, to_node FROM edges WHERE from_node=? OR to_node=?",
                (node_id, node_id),
            ).fetchall()
            if all(
                row["from_node"] in auto_candidate_ids
                and row["to_node"] in auto_candidate_ids
                for row in remaining_edges
            ):
                removable_auto_ids.add(node_id)
        if removable_auto_ids:
            placeholders = ",".join("?" * len(removable_auto_ids))
            params = list(removable_auto_ids)
            conn.execute(
                f"DELETE FROM edges WHERE from_node IN ({placeholders}) OR to_node IN ({placeholders})",
                params * 2,
            )
            conn.execute(f"DELETE FROM nodes WHERE id IN ({placeholders})", params)
            self._v2_delete_nodes(conn, params)

        metadata = {
            "source": "local_folder",
            "source_id": source_id,
            "root_path": str(root),
            "file_path": str(file_path),
            "relative_path": relative_path,
            "filename": file_path.name,
            "ext": file_path.suffix.lower(),
            "category": category,
            "parser_type": parser_type,
            "bytes": stat.st_size,
            "modified_at": _safe_iso_from_stat_mtime(stat.st_mtime),
            "sha256": sha256,
            "parser": parser_meta,
            "workspace_id": workspace_id,
        }
        self._upsert_node(
            conn,
            file_node_id,
            _node_type_for_category(category),
            file_path.name,
            summary=text[:700],
            metadata=metadata,
            raw=metadata,
            owner=user_email,
            workspace_id=workspace_id,
        )
        self._upsert_edge(
            conn,
            parent_folder_id,
            file_node_id,
            "포함함",
            weight=1.0,
            metadata={"source": "local_scan", "workspace_id": workspace_id},
        )
        self._cleanup_local_graph_orphans(conn, source_id)

        target_for_concepts = text
        if category == "image" and text:
            image_text_id = f"imagetext:{_sha256_text(f'{file_node_id}:ocr')[:24]}"
            self._upsert_node(
                conn,
                image_text_id,
                "ImageText",
                f"{file_path.name} OCR",
                summary=_clean_text(text)[:700],
                metadata={
                    "source_node": file_node_id,
                    "source_id": source_id,
                    "chars": len(text),
                    "workspace_id": workspace_id,
                },
                owner=user_email,
                workspace_id=workspace_id,
            )
            self._upsert_edge(
                conn,
                file_node_id,
                image_text_id,
                "포함함",
                weight=0.8,
                metadata={"source": "ocr", "workspace_id": workspace_id},
            )

        # Typed chunking by file extension (markdown/code/plain); plain files
        # keep legacy _chunks boundaries and therefore identical chunk ids.
        chunk_strategy = chunk_strategy_for(file_path.name)
        for index, piece in enumerate(typed_chunks(text, strategy=chunk_strategy)):
            chunk = piece["text"]
            chunk_fields = typed_chunk_meta_fields(piece)
            chunk_id = f"chunk:{_sha256_text(f'{file_node_id}:{index}:{chunk}')[:24]}"
            self._upsert_node(
                conn,
                chunk_id,
                "Chunk",
                f"{file_path.name} chunk {index + 1}",
                summary=chunk[:500],
                metadata={
                    "index": index,
                    "source_node": file_node_id,
                    "source_id": source_id,
                    "workspace_id": workspace_id,
                    **chunk_fields,
                },
                owner=user_email,
                workspace_id=workspace_id,
            )
            self._upsert_chunk(
                conn,
                chunk_id=chunk_id,
                source_node=file_node_id,
                text=chunk,
                metadata={
                    "index": index,
                    "source_node": file_node_id,
                    "source_id": source_id,
                    "workspace_id": workspace_id,
                    **chunk_fields,
                },
            )
            self._upsert_edge(
                conn,
                file_node_id,
                chunk_id,
                "포함함",
                weight=0.7,
                metadata={"source": "local_scan", "workspace_id": workspace_id},
            )

        concepts = _extract_concepts(target_for_concepts, limit=18)
        concept_ids: Dict[str, str] = {}
        for concept in concepts:
            node_t = _classify_node_type(concept, target_for_concepts)
            concept_id = _local_scoped_slug(node_t.lower(), concept, workspace_id)
            concept_ids[concept.lower()] = concept_id
            self._upsert_node(
                conn,
                concept_id,
                node_t,
                concept,
                metadata={
                    "auto_extracted": True,
                    "source": "local_folder",
                    "source_id": source_id,
                    "workspace_id": workspace_id,
                },
                owner=user_email,
                workspace_id=workspace_id,
            )
            self._upsert_edge(
                conn,
                file_node_id,
                concept_id,
                "언급함",
                weight=0.75,
                metadata={"source": "local_scan", "workspace_id": workspace_id},
            )

        for triple in _extract_triples(target_for_concepts, concepts, limit=20):
            subj_id = concept_ids.get(triple["subject"].lower())
            obj_id = concept_ids.get(triple["object"].lower())
            if subj_id and obj_id and subj_id != obj_id:
                self._upsert_edge(
                    conn,
                    subj_id,
                    obj_id,
                    triple["relation"],
                    weight=0.9,
                    metadata={
                        "context": triple.get("context", "")[:240],
                        "source_id": source_id,
                        "workspace_id": workspace_id,
                    },
                )

        for item in _semantic_items(target_for_concepts):
            sem_type = item["type"]
            sem_title = item["title"]
            sem_id = f"{sem_type.lower()}:{_sha256_text(f'{file_node_id}:{sem_type}:{sem_title}')[:24]}"
            self._upsert_node(
                conn,
                sem_id,
                sem_type,
                sem_title,
                summary=item["summary"],
                metadata={
                    "auto_extracted": True,
                    "source_node": file_node_id,
                    "filename": file_path.name,
                    "workspace_id": workspace_id,
                },
                raw=item,
                owner=user_email,
                workspace_id=workspace_id,
            )
            self._upsert_edge(
                conn,
                file_node_id,
                sem_id,
                "포함함",
                weight=0.9,
                metadata={"source": "local_scan", "workspace_id": workspace_id},
            )

        return file_node_id
