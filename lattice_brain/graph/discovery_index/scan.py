"""``index_local_folder``: the driver that walks a folder into the graph.

Decides per file whether to skip, refresh, or fully re-index, and reports
counts, errors, and the honest notice about what was converted. Moved
verbatim out of ``discovery_index.py`` (v11.3.0 decomposition).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# ruff: noqa: F403,F405
from .._kg_common import *  # noqa: F403,F401

# Typing-only base (runtime value is `object`, so the store's MRO is
# unchanged). The driver calls all three other halves through `self` — text
# extraction, the row/node upserts, and the skip checks — so they are named as
# bases rather than re-declared here, where their signatures could drift. The
# store contract (`_connect`, `_upsert_node`, …) arrives with them.
if TYPE_CHECKING:
    from .cleanup import _LocalCleanupMixin
    from .extract import _LocalExtractMixin
    from .upsert import _LocalUpsertMixin

    class _Core(_LocalExtractMixin, _LocalUpsertMixin, _LocalCleanupMixin):
        """The sibling halves this one reaches through ``self``."""
else:
    _Core = object


class _LocalScanMixin(_Core):
    """The folder-indexing driver. Composed into the public mixin."""

    def index_local_folder(
        self,
        path: Path,
        *,
        include_ocr: bool = False,
        watch_enabled: bool = False,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
        consent: Optional[Dict[str, Any]] = None,
        max_files: int = 5_000,
        source_id_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Read approved files from a local folder and connect them to Graph RAG."""
        root = Path(path).expanduser().resolve()
        if not root.exists():
            raise ValueError(f"경로가 존재하지 않습니다: {path}")
        if not root.is_dir():
            raise ValueError(f"폴더가 아닙니다: {path}")

        os_type = _current_os_type()
        drive_id = _drive_id_for_path(root)
        path_fingerprint = _path_fingerprint(root)
        source_id = str(source_id_override or "").strip()
        if not source_id:
            source_id = (
                f"source:{_sha256_text(f'{workspace_id}|{path_fingerprint}')[:24]}"
                if workspace_id
                else f"source:{path_fingerprint}"
            )
        now = _now()
        max_files = max(1, min(int(max_files or 5_000), 50_000))
        consent_payload = {
            "approved_at": now,
            "knowledge_source": True,
            "include_ocr": bool(include_ocr),
            "watch_enabled": bool(watch_enabled),
            "sensitive_files_default_excluded": True,
            **(consent or {}),
            "approved_by": user_email or (consent or {}).get("approved_by"),
            "workspace_id": workspace_id or (consent or {}).get("workspace_id"),
        }
        counts: Counter = Counter()
        seen_relative_paths: set = set()
        indexed_nodes: List[str] = []
        errors: List[Dict[str, str]] = []
        limit_reached = False

        with self._connect() as conn:
            existing_source = conn.execute(
                "SELECT id, consent_json FROM knowledge_sources WHERE root_path=?",
                (str(root),),
            ).fetchone()
            if existing_source is not None:
                existing_consent = _safe_loads(existing_source["consent_json"])
                existing_scope = existing_consent.get("workspace_id") or "personal"
                requested_scope = workspace_id or consent_payload.get("workspace_id") or "personal"
                if existing_scope != requested_scope:
                    raise ValueError(
                        "This folder is already connected to another workspace. "
                        "Disconnect it there before assigning it to a different Brain."
                    )
                if existing_source["id"] != source_id:
                    # Reuse the legacy source identity so a personal source is
                    # reprojected in place instead of duplicated during upgrade.
                    source_id = existing_source["id"]
            conn.execute(
                """
                    INSERT INTO knowledge_sources(
                      id, root_path, os_type, drive_id, label, status, include_ocr,
                      watch_enabled, consent_json, created_at, updated_at, last_scanned_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                      root_path=excluded.root_path,
                      os_type=excluded.os_type,
                      drive_id=excluded.drive_id,
                      label=excluded.label,
                      status=excluded.status,
                      include_ocr=excluded.include_ocr,
                      watch_enabled=excluded.watch_enabled,
                      consent_json=excluded.consent_json,
                      updated_at=excluded.updated_at,
                      last_scanned_at=excluded.last_scanned_at
                    """,
                (
                    source_id,
                    str(root),
                    os_type,
                    drive_id,
                    root.name or str(root),
                    "scanning",
                    1 if include_ocr else 0,
                    1 if watch_enabled else 0,
                    _json(consent_payload),
                    now,
                    now,
                    now,
                ),
            )

            for entry in self._iter_local_scan_entries(root, max_files=max_files):
                kind = entry["kind"]
                file_path = entry["path"]
                if kind == "limit_reached":
                    counts["limit_reached"] += 1
                    limit_reached = True
                    break
                if kind in {"excluded_dir", "excluded"}:
                    counts["excluded"] += 1
                    continue
                if kind in {"inaccessible_dir", "inaccessible_file"}:
                    counts["failed"] += 1
                    errors.append(
                        {
                            "path": str(file_path),
                            "error": entry.get("reason", "inaccessible"),
                        }
                    )
                    continue
                if kind != "file":
                    continue

                stat = entry["stat"]
                try:
                    relative_path = file_path.relative_to(root).as_posix()
                except ValueError:
                    relative_path = file_path.name
                seen_relative_paths.add(relative_path)
                modified_at = _safe_iso_from_stat_mtime(stat.st_mtime)
                existing = conn.execute(
                    """
                        SELECT size_bytes, modified_at, sha256, graph_node_id, status, metadata_json
                        FROM local_file_index
                        WHERE source_id=? AND relative_path=?
                        """,
                    (source_id, relative_path),
                ).fetchone()
                decision = self._local_file_decision(file_path, root, stat)
                parser_type = decision["parser_type"]
                if not decision["indexable"]:
                    counts[decision["status"]] += 1
                    if existing and existing["graph_node_id"]:
                        self._delete_local_file_graph(conn, existing["graph_node_id"])
                    self._upsert_local_file_index(
                        conn,
                        source_id=source_id,
                        root=root,
                        file_path=file_path,
                        stat=stat,
                        os_type=os_type,
                        drive_id=drive_id,
                        status=decision["status"],
                        parser_type=parser_type,
                        metadata={
                            "reason": decision["reason"],
                            "category": decision["category"],
                        },
                    )
                    continue

                if (
                    existing
                    and existing["status"] == "indexed"
                    and existing["graph_node_id"]
                    and self._local_file_index_has_extracted_text(existing)
                    and self._node_matches_workspace(
                        conn, existing["graph_node_id"], workspace_id
                    )
                    and existing["size_bytes"] == stat.st_size
                    and existing["modified_at"] == modified_at
                ):
                    counts["skipped_unchanged"] += 1
                    self._upsert_local_file_index(
                        conn,
                        source_id=source_id,
                        root=root,
                        file_path=file_path,
                        stat=stat,
                        os_type=os_type,
                        drive_id=drive_id,
                        status="indexed",
                        parser_type=parser_type,
                        sha256=existing["sha256"],
                        graph_node_id=existing["graph_node_id"],
                        metadata={
                            **_safe_loads(existing["metadata_json"]),
                            "category": decision["category"],
                            "unchanged": True,
                        },
                    )
                    continue

                try:
                    data = file_path.read_bytes()
                    digest = _sha256_bytes(data)
                except Exception as exc:
                    counts["failed"] += 1
                    errors.append({"path": str(file_path), "error": str(exc)})
                    if existing and existing["graph_node_id"]:
                        self._delete_local_file_graph(conn, existing["graph_node_id"])
                    self._upsert_local_file_index(
                        conn,
                        source_id=source_id,
                        root=root,
                        file_path=file_path,
                        stat=stat,
                        os_type=os_type,
                        drive_id=drive_id,
                        status="failed",
                        parser_type=parser_type,
                        error_message=str(exc),
                        metadata={"category": decision["category"]},
                    )
                    continue

                if (
                    existing
                    and existing["sha256"] == digest
                    and existing["graph_node_id"]
                    and self._local_file_index_has_extracted_text(existing)
                    and self._node_matches_workspace(
                        conn, existing["graph_node_id"], workspace_id
                    )
                ):
                    counts["skipped_unchanged"] += 1
                    self._upsert_local_file_index(
                        conn,
                        source_id=source_id,
                        root=root,
                        file_path=file_path,
                        stat=stat,
                        os_type=os_type,
                        drive_id=drive_id,
                        status="indexed",
                        parser_type=parser_type,
                        sha256=digest,
                        graph_node_id=existing["graph_node_id"],
                        metadata={
                            **_safe_loads(existing["metadata_json"]),
                            "category": decision["category"],
                            "sha256_unchanged": True,
                        },
                    )
                    continue

                try:
                    text, parser_meta = self._extract_local_file_text(
                        file_path,
                        decision["category"],
                        include_ocr=include_ocr,
                    )
                    text = _clean_text(text)
                    parser_meta = {**parser_meta, "extracted_chars": len(text)}
                    if not text:
                        counts["skipped_empty_text"] += 1
                        if existing and existing["graph_node_id"]:
                            self._delete_local_file_graph(
                                conn, existing["graph_node_id"]
                            )
                        self._upsert_local_file_index(
                            conn,
                            source_id=source_id,
                            root=root,
                            file_path=file_path,
                            stat=stat,
                            os_type=os_type,
                            drive_id=drive_id,
                            status="skipped_empty_text",
                            parser_type=parser_type,
                            sha256=digest,
                            error_message="텍스트 추출 결과가 비어 있습니다.",
                            metadata={
                                "category": decision["category"],
                                "parser": parser_meta,
                            },
                        )
                        continue
                    graph_node_id = self._upsert_local_file_node(
                        conn,
                        source_id=source_id,
                        root=root,
                        file_path=file_path,
                        stat=stat,
                        os_type=os_type,
                        drive_id=drive_id,
                        sha256=digest,
                        category=decision["category"],
                        parser_type=parser_type,
                        text=text,
                        parser_meta=parser_meta,
                        user_email=user_email,
                        workspace_id=workspace_id,
                    )
                    self._upsert_local_file_index(
                        conn,
                        source_id=source_id,
                        root=root,
                        file_path=file_path,
                        stat=stat,
                        os_type=os_type,
                        drive_id=drive_id,
                        status="indexed",
                        parser_type=parser_type,
                        sha256=digest,
                        graph_node_id=graph_node_id,
                        metadata={
                            "category": decision["category"],
                            "parser": parser_meta,
                        },
                    )
                    counts["indexed"] += 1
                    indexed_nodes.append(graph_node_id)
                except Exception as exc:
                    counts["failed"] += 1
                    errors.append({"path": str(file_path), "error": str(exc)})
                    if existing and existing["graph_node_id"]:
                        self._delete_local_file_graph(conn, existing["graph_node_id"])
                    self._upsert_local_file_index(
                        conn,
                        source_id=source_id,
                        root=root,
                        file_path=file_path,
                        stat=stat,
                        os_type=os_type,
                        drive_id=drive_id,
                        status="failed",
                        parser_type=parser_type,
                        sha256=digest,
                        error_message=str(exc),
                        metadata={"category": decision["category"]},
                    )

            if not limit_reached:
                existing_rows = {
                    row["relative_path"]: row["graph_node_id"]
                    for row in conn.execute(
                        "SELECT relative_path, graph_node_id FROM local_file_index WHERE source_id=?",
                        (source_id,),
                    )
                }
                deleted_paths = set(existing_rows) - seen_relative_paths
                for relative_path in deleted_paths:
                    self._delete_local_file_graph(
                        conn, existing_rows.get(relative_path)
                    )
                    conn.execute(
                        """
                            UPDATE local_file_index
                            SET status='deleted', deleted=1, last_scanned_at=?, error_message=NULL, graph_node_id=NULL
                            WHERE source_id=? AND relative_path=?
                            """,
                        (_now(), source_id, relative_path),
                    )
                counts["deleted"] = len(deleted_paths)
            conn.execute(
                """
                    UPDATE knowledge_sources
                    SET status='active', updated_at=?, last_scanned_at=?
                    WHERE id=?
                    """,
                (_now(), _now(), source_id),
            )

        return {
            "status": "ok",
            "source": {
                "id": source_id,
                "root_path": str(root),
                "os_type": os_type,
                "drive_id": drive_id,
                "include_ocr": bool(include_ocr),
                "watch_enabled": bool(watch_enabled),
            },
            "counts": dict(counts),
            "indexed_nodes": indexed_nodes[:100],
            "errors": errors[:50],
            "notice": "Lattice AI는 사용자가 선택한 폴더만 AI 지식으로 변환합니다.",
        }
