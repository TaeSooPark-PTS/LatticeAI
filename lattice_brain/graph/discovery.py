from __future__ import annotations

from ..quiet import quiet

# ruff: noqa: F403,F405
from ._kg_common import *  # noqa: F403,F401


class KnowledgeGraphDiscoveryMixin:
    def discover_local_roots(self) -> Dict[str, Any]:
        """Return safe, cross-platform starting points for structure browsing."""
        os_type = _current_os_type()
        home = Path.home().expanduser()
        roots: List[Dict[str, Any]] = []
        seen: set = set()

        def add(
            label: str,
            path: Path,
            kind: str,
            *,
            recommended: bool = True,
            warning: Optional[str] = None,
        ) -> None:
            try:
                resolved = path.expanduser().resolve()
            except OSError:
                resolved = path.expanduser()
            key = str(resolved)
            if key in seen or not resolved.exists():
                return
            seen.add(key)
            roots.append(
                {
                    "id": f"{kind}:{_path_fingerprint(resolved)}",
                    "label": label,
                    "path": key,
                    "kind": kind,
                    "recommended": recommended,
                    "warning": warning or _root_warning(resolved, os_type),
                }
            )

        add("홈", home, "home", warning=_root_warning(home, os_type))
        for name, label in (
            ("Documents", "문서"),
            ("Desktop", "데스크탑"),
            ("Downloads", "다운로드"),
            ("Pictures", "사진"),
            ("Projects", "프로젝트"),
        ):
            add(label, home / name, name.lower())

        if os_type == "macos":
            volumes = Path("/Volumes")
            if volumes.exists():
                try:
                    for volume in sorted(
                        volumes.iterdir(), key=lambda p: p.name.lower()
                    ):
                        add(volume.name, volume, "volume", recommended=False)
                except OSError:
                    quiet()
        elif os_type == "windows":
            for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
                drive = Path(f"{letter}:\\")
                if drive.exists():
                    add(
                        f"{letter}: 드라이브",
                        drive,
                        "drive",
                        recommended=(letter != "C"),
                    )
            for env_name, label in (
                ("OneDrive", "OneDrive"),
                ("OneDriveCommercial", "OneDrive"),
            ):
                raw = os.environ.get(env_name)
                if raw:
                    add(label, Path(raw), "cloud", recommended=False)
        elif os_type == "linux":
            for base in (Path("/mnt"), Path("/media")):
                add(str(base), base, "mounts", recommended=False)
                try:
                    if base.exists():
                        for mounted in sorted(
                            base.iterdir(), key=lambda p: p.name.lower()
                        ):
                            add(mounted.name, mounted, "volume", recommended=False)
                except OSError:
                    quiet()

        return {
            "os_type": os_type,
            "computer": platform.node() or "local",
            "roots": roots,
            "privacy_notice": "처음에는 드라이브와 폴더 구조만 확인하며, 파일 내용은 사용자가 동의한 뒤에만 읽습니다.",
        }

    def preview_local_tree(self, path: Path, *, max_items: int = 200) -> Dict[str, Any]:
        """List one folder level using metadata only; file contents are not read."""
        root = Path(path).expanduser().resolve()
        if not root.exists():
            raise ValueError(f"경로가 존재하지 않습니다: {path}")
        if not root.is_dir():
            raise ValueError(f"폴더가 아닙니다: {path}")

        os_type = _current_os_type()
        max_items = max(1, min(int(max_items or 200), 1000))
        items: List[Dict[str, Any]] = []
        inaccessible = 0
        try:
            children = sorted(
                root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())
            )
        except PermissionError as exc:
            return {
                "path": str(root),
                "items": [],
                "error": f"접근 권한 없음: {exc}",
                "privacy_notice": "현재 단계에서는 파일 내용을 읽지 않고, 폴더와 파일의 이름/크기/수정일만 확인합니다.",
            }

        for child in children[:max_items]:
            try:
                is_dir = child.is_dir()
                stat = child.stat()
                reason = (
                    _excluded_directory_reason(child, root=root, os_type=os_type)
                    if is_dir
                    else _sensitive_file_reason(child, root=root)
                )
                items.append(
                    {
                        "name": child.name,
                        "path": str(child),
                        "type": "directory" if is_dir else "file",
                        "extension": "" if is_dir else child.suffix.lower(),
                        "size_bytes": None if is_dir else stat.st_size,
                        "modified_at": _safe_iso_from_stat_mtime(stat.st_mtime),
                        "hidden": _is_hidden_path(child, root),
                        "accessible": True,
                        "excluded_reason": reason,
                    }
                )
            except PermissionError:
                inaccessible += 1
                items.append(
                    {
                        "name": child.name,
                        "path": str(child),
                        "type": "unknown",
                        "accessible": False,
                        "excluded_reason": "permission_denied",
                    }
                )
            except OSError as exc:
                inaccessible += 1
                items.append(
                    {
                        "name": child.name,
                        "path": str(child),
                        "type": "unknown",
                        "accessible": False,
                        "excluded_reason": str(exc),
                    }
                )

        return {
            "path": str(root),
            "os_type": os_type,
            "items": items,
            "truncated": len(children) > max_items,
            "inaccessible": inaccessible,
            "warning": _root_warning(root, os_type),
            "privacy_notice": "현재 단계에서는 파일 내용을 읽지 않고, 폴더와 파일의 이름/크기/수정일만 확인합니다.",
        }

    def _iter_local_scan_entries(
        self, root: Path, *, max_files: int
    ) -> Iterable[Dict[str, Any]]:
        os_type = _current_os_type()
        stack = [root]
        files_seen = 0
        while stack:
            current = stack.pop()
            try:
                children = sorted(
                    current.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())
                )
            except PermissionError as exc:
                yield {
                    "kind": "inaccessible_dir",
                    "path": current,
                    "reason": f"permission_denied: {exc}",
                }
                continue
            except OSError as exc:
                yield {"kind": "inaccessible_dir", "path": current, "reason": str(exc)}
                continue

            for child in children:
                if child.is_symlink():
                    yield {"kind": "excluded", "path": child, "reason": "symlink"}
                    continue
                try:
                    if child.is_dir():
                        reason = _excluded_directory_reason(
                            child, root=root, os_type=os_type
                        )
                        if reason:
                            yield {
                                "kind": "excluded_dir",
                                "path": child,
                                "reason": reason,
                            }
                        else:
                            stack.append(child)
                        continue
                    if not child.is_file():
                        yield {
                            "kind": "excluded",
                            "path": child,
                            "reason": "not_regular_file",
                        }
                        continue
                    stat = child.stat()
                except PermissionError as exc:
                    yield {
                        "kind": "inaccessible_file",
                        "path": child,
                        "reason": f"permission_denied: {exc}",
                    }
                    continue
                except OSError as exc:
                    yield {
                        "kind": "inaccessible_file",
                        "path": child,
                        "reason": str(exc),
                    }
                    continue

                files_seen += 1
                if files_seen > max_files:
                    yield {
                        "kind": "limit_reached",
                        "path": child,
                        "reason": "max_files",
                    }
                    return
                yield {"kind": "file", "path": child, "stat": stat}

    def _local_file_decision(
        self, path: Path, root: Path, stat: os.stat_result
    ) -> Dict[str, Any]:
        ext = path.suffix.lower()
        category = _file_category(ext)
        parser_type = _parser_type_for_category(category, ext)
        sensitive_reason = _sensitive_file_reason(path, root=root)
        if sensitive_reason:
            return {
                "status": "sensitive_blocked",
                "reason": sensitive_reason,
                "category": category,
                "parser_type": parser_type,
                "indexable": False,
            }
        if category == "unsupported":
            return {
                "status": "unsupported",
                "reason": "unsupported_extension",
                "category": category,
                "parser_type": parser_type,
                "indexable": False,
            }
        limit = _size_limit_for_category(category)
        if stat.st_size > limit:
            return {
                "status": "too_large",
                "reason": f"size>{limit}",
                "category": category,
                "parser_type": parser_type,
                "indexable": False,
            }
        return {
            "status": "pending",
            "reason": "",
            "category": category,
            "parser_type": parser_type,
            "indexable": True,
        }

    def audit_local_folder(
        self, path: Path, *, include_ocr: bool = False, max_files: int = 50_000
    ) -> Dict[str, Any]:
        """Safety-check a folder using metadata only; file bodies are not read."""
        root = Path(path).expanduser().resolve()
        if not root.exists():
            raise ValueError(f"경로가 존재하지 않습니다: {path}")
        if not root.is_dir():
            raise ValueError(f"폴더가 아닙니다: {path}")

        os_type = _current_os_type()
        max_files = max(1, min(int(max_files or 50_000), 200_000))
        status_counts: Counter = Counter()
        category_counts: Counter = Counter()
        extension_counts: Counter = Counter()
        allowed_samples: List[Dict[str, Any]] = []
        excluded_samples: List[Dict[str, Any]] = []
        total_files = 0
        readable_files = 0
        inaccessible = 0
        excluded_dirs = 0
        limit_reached = False

        for entry in self._iter_local_scan_entries(root, max_files=max_files):
            kind = entry["kind"]
            path_obj = entry["path"]
            if kind == "limit_reached":
                limit_reached = True
                break
            if kind == "excluded_dir":
                excluded_dirs += 1
                if len(excluded_samples) < 25:
                    excluded_samples.append(
                        _sample_file(
                            path_obj, root, "excluded", entry.get("reason", "")
                        )
                    )
                continue
            if kind in {"inaccessible_dir", "inaccessible_file"}:
                inaccessible += 1
                status_counts["failed"] += 1
                if len(excluded_samples) < 25:
                    excluded_samples.append(
                        _sample_file(path_obj, root, "failed", entry.get("reason", ""))
                    )
                continue
            if kind == "excluded":
                status_counts["excluded"] += 1
                if len(excluded_samples) < 25:
                    excluded_samples.append(
                        _sample_file(
                            path_obj, root, "excluded", entry.get("reason", "")
                        )
                    )
                continue
            if kind != "file":
                continue

            total_files += 1
            stat = entry["stat"]
            decision = self._local_file_decision(path_obj, root, stat)
            status = decision["status"]
            category = decision["category"]
            ext = path_obj.suffix.lower() or "(none)"
            category_counts[category] += 1
            extension_counts[ext] += 1
            if decision["indexable"]:
                readable_files += 1
                status_counts["readable"] += 1
                if len(allowed_samples) < 25:
                    allowed_samples.append(_sample_file(path_obj, root, "readable"))
            else:
                status_counts[status] += 1
                if len(excluded_samples) < 25:
                    excluded_samples.append(
                        _sample_file(path_obj, root, status, decision["reason"])
                    )

        doc_weight = (
            category_counts["pdf"] * 1.4
            + category_counts["document"] * 0.9
            + category_counts["slide_deck"] * 1.0
        )
        sheet_weight = category_counts["spreadsheet"] * 0.6
        ocr_weight = category_counts["image"] * (1.8 if include_ocr else 0.1)
        estimated_seconds = round(
            readable_files * 0.04 + doc_weight + sheet_weight + ocr_weight, 1
        )

        return {
            "path": str(root),
            "source_id": f"source:{_path_fingerprint(root)}",
            "os_type": os_type,
            "drive_id": _drive_id_for_path(root),
            "warning": _root_warning(root, os_type),
            "privacy_notice": "현재 단계에서는 파일 내용을 읽지 않고, 폴더와 파일의 이름/크기/수정일만 확인합니다.",
            "include_ocr_requested": bool(include_ocr),
            "summary": {
                "total_files": total_files,
                "readable_files": readable_files,
                "excluded_files": int(
                    status_counts["excluded"]
                    + status_counts["sensitive_blocked"]
                    + status_counts["too_large"]
                    + status_counts["unsupported"]
                ),
                "sensitive_files": int(status_counts["sensitive_blocked"]),
                "too_large_files": int(status_counts["too_large"]),
                "unsupported_files": int(status_counts["unsupported"]),
                "image_ocr_candidates": int(category_counts["image"]),
                "inaccessible_items": inaccessible,
                "excluded_dirs": excluded_dirs,
                "estimated_seconds": estimated_seconds,
                "storage_root": str(self.db_path.parent),
                "limit_reached": limit_reached,
            },
            "by_status": dict(status_counts),
            "by_category": dict(category_counts),
            "by_extension": dict(extension_counts.most_common(40)),
            "allowed_samples": allowed_samples,
            "excluded_samples": excluded_samples,
            "consent_required": {
                "knowledge_source": True,
                "image_ocr": bool(category_counts["image"]),
                "watch": True,
                "sensitive_files_default_excluded": True,
            },
        }

    def local_sources(self) -> Dict[str, Any]:
        with self._connect() as conn:
            sources = [
                {
                    "id": row["id"],
                    "root_path": row["root_path"],
                    "os_type": row["os_type"],
                    "drive_id": row["drive_id"],
                    "label": row["label"],
                    "status": row["status"],
                    "include_ocr": bool(row["include_ocr"]),
                    "watch_enabled": bool(row["watch_enabled"]),
                    "consent": _safe_loads(row["consent_json"]),
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "last_scanned_at": row["last_scanned_at"],
                }
                for row in conn.execute(
                    """
                        SELECT id, root_path, os_type, drive_id, label, status, include_ocr,
                               watch_enabled, consent_json, created_at, updated_at, last_scanned_at
                        FROM knowledge_sources
                        ORDER BY updated_at DESC, id ASC
                        """
                )
            ]
            status_rows = conn.execute(
                "SELECT source_id, status, COUNT(*) AS count FROM local_file_index GROUP BY source_id, status"
            ).fetchall()
        counts: Dict[str, Dict[str, int]] = {}
        for row in status_rows:
            counts.setdefault(row["source_id"], {})[row["status"]] = row["count"]
        for source in sources:
            source["file_status"] = counts.get(source["id"], {})
        return {"sources": sources}

    def local_source_health(self, *, error_samples: int = 3) -> Dict[str, Any]:
        """Per-folder memory state (v9.9.7).

        ``local_sources`` answers "which folders are connected"; a user asking
        "is this folder actually in my Brain?" needs three more facts per
        folder, and this is the single read that provides them:

        * **coverage** — indexed / known files, so a half-indexed folder is
          visible instead of looking connected;
        * **failed / skipped counts** — what did not make it in;
        * **recent error samples** — *why*, with the stored message.

        Vector freshness is deliberately **not** claimed per folder: the vector
        index is global, so a per-folder number would be invented. Callers pair
        this with ``vector_freshness()`` and label it as global.
        """
        try:
            samples = max(0, min(int(error_samples), 20))
        except (TypeError, ValueError):
            samples = 3
        payload = self.local_sources()
        sources = payload.get("sources") or []
        by_id = {str(source.get("id")): source for source in sources}
        errors: Dict[str, List[Dict[str, Any]]] = {}
        if by_id:
            with self._connect() as conn:
                for source_id in by_id:
                    rows = conn.execute(
                        """
                            SELECT relative_path, status, error_message, last_scanned_at
                            FROM local_file_index
                            WHERE source_id=? AND error_message IS NOT NULL AND error_message<>''
                            ORDER BY last_scanned_at DESC
                            LIMIT ?
                            """,
                        (source_id, samples or 1),
                    ).fetchall()
                    if samples:
                        errors[source_id] = [
                            {
                                "path": row["relative_path"],
                                "status": row["status"],
                                "detail": str(row["error_message"] or "")[:300],
                                "at": row["last_scanned_at"],
                            }
                            for row in rows
                        ]

        folders: List[Dict[str, Any]] = []
        for source in sources:
            counts = source.get("file_status") or {}
            total = sum(int(value or 0) for value in counts.values())
            indexed = int(counts.get("indexed") or 0)
            failed = int(counts.get("failed") or 0) + int(counts.get("error") or 0)
            skipped = int(counts.get("skipped") or 0)
            folders.append({
                "id": source.get("id"),
                "label": source.get("label") or source.get("root_path"),
                "root_path": source.get("root_path"),
                "status": source.get("status"),
                "watch_enabled": bool(source.get("watch_enabled")),
                "last_scanned_at": source.get("last_scanned_at"),
                "files": {
                    "total": total,
                    "indexed": indexed,
                    "failed": failed,
                    "skipped": skipped,
                    "pending": max(0, total - indexed - failed - skipped),
                },
                # None (not 0) when nothing is known yet — an empty folder must
                # not read as "0% indexed".
                "coverage": round(indexed / total, 4) if total else None,
                "recent_errors": errors.get(str(source.get("id")), []),
            })
        return {"folders": folders, "count": len(folders)}

    def set_local_source_watch(self, source_id: str, enabled: bool) -> Dict[str, Any]:
        source_id = str(source_id or "").strip()
        if not source_id:
            raise ValueError("source_id required")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM knowledge_sources WHERE id=?",
                (source_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"knowledge source not found: {source_id}")
            conn.execute(
                "UPDATE knowledge_sources SET watch_enabled=?, updated_at=? WHERE id=?",
                (1 if enabled else 0, _now(), source_id),
            )
        return {"source_id": source_id, "watch_enabled": bool(enabled)}

    def remove_local_source(self, source_id: str) -> Dict[str, Any]:
        """Remove one approved local source and its derived graph projection.

        This is intentionally non-destructive for user files: only the LatticeAI
        index rows, graph nodes, edges, and chunks derived from the source are
        removed. The original folder and files are never touched.
        """
        source_id = str(source_id or "").strip()
        if not source_id:
            raise ValueError("source_id required")
        with self._connect() as conn:
            source = conn.execute(
                "SELECT id, root_path FROM knowledge_sources WHERE id=?",
                (source_id,),
            ).fetchone()
            if not source:
                raise ValueError(f"knowledge source not found: {source_id}")
            rows = conn.execute(
                "SELECT graph_node_id FROM local_file_index WHERE source_id=? AND graph_node_id IS NOT NULL",
                (source_id,),
            ).fetchall()
            graph_node_ids = [
                row["graph_node_id"] for row in rows if row["graph_node_id"]
            ]
            for graph_node_id in graph_node_ids:
                self._delete_local_file_graph(conn, graph_node_id)
            conn.execute("DELETE FROM local_file_index WHERE source_id=?", (source_id,))
            conn.execute("DELETE FROM knowledge_sources WHERE id=?", (source_id,))
            self._cleanup_local_graph_orphans(conn, source_id)
        return {
            "source_id": source_id,
            "root_path": source["root_path"],
            "removed_graph_nodes": len(graph_node_ids),
        }
