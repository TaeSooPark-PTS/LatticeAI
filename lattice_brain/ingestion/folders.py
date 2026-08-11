"""Walking a folder, and the one-page web hand-off, into the standard door.

Neither method is a second ingest path: both build ordinary ``IngestionItem``
values and hand them to ``IngestionPipeline.ingest``. The folder walk owns the
filtering order (hard skip-list → hidden → ``.latticeignore`` → extension →
size) and the choice between ingesting inline and scheduling in the background;
the web hand-off owns the refusal to fetch or parse anything itself.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from ._contract import IngestionCore as _Core
from .constants import (
    DEFAULT_FOLDER_EXTENSIONS,
    DEFAULT_MAX_FILE_BYTES,
    FOLDER_DEFAULT_SKIP_DIRS,
    FOLDER_DOCUMENT_EXTENSIONS,
    FOLDER_MULTIMODAL_EXTENSIONS,
    FOLDER_VIDEO_EXTENSIONS,
    LATTICEIGNORE_FILENAME,
)
from .folder_scan import _load_latticeignore, _matches_ignore
from .models import IngestionItem, IngestionResult


class IngestionFolderMixin(_Core):
    """Folder walk + web hand-off. Mixed into ``IngestionPipeline``."""

    def ingest_web_page(
        self,
        url: str,
        extracted_text: str,
        *,
        title: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        owner: Optional[str] = None,
        workspace_id: Optional[str] = None,
        captured_at: Optional[str] = None,
        user_email: Optional[str] = None,
    ) -> IngestionResult:
        """Ingest an *already-extracted* web page (see module docstring seam).

        Fetching/parsing is upstream's responsibility (browser extension /
        tools layer); this wrapper only normalizes ``(url, extracted_text)``
        into an ``IngestionItem(source_type="web_url")`` and routes it through
        the standard :meth:`ingest` door.
        """
        url = str(url or "").strip()
        if not url:
            return IngestionResult(
                status="failed", source_type="web_url",
                indexing_status="skipped", detail="url required",
            )
        text = str(extracted_text or "")
        if not text.strip():
            return IngestionResult(
                status="failed", source_type="web_url",
                indexing_status="skipped",
                detail=(
                    "extracted_text required — the graph layer does not fetch or "
                    "parse the web; extraction happens upstream."
                ),
            )
        item = IngestionItem(
            source_type="web_url",
            title=title or url,
            text=text,
            source_uri=url,
            owner=owner,
            workspace_id=workspace_id,
            captured_at=captured_at,
            metadata=dict(metadata or {}),
        )
        return self.ingest(item, user_email=user_email or owner)

    def ingest_folder(
        self,
        root_path: Any,
        *,
        recursive: bool = True,
        background: bool = False,
        extensions: Optional[Iterable[str]] = None,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        include_hidden: bool = False,
        max_files: int = 1000,
        max_errors: int = 25,
        owner: Optional[str] = None,
        workspace_id: Optional[str] = None,
        user_email: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Walk ``root_path`` and ingest every eligible file through the pipeline.

        Filtering, in order: hard skip-list directories (``.git`` …), hidden
        entries (unless ``include_hidden``), root ``.latticeignore`` patterns
        (fnmatch globs; ``dir/`` suffix prunes directories), extension
        allow-list, then ``max_file_bytes``. Text/code files are read inline so
        their content is chunked; ``.pdf`` routes through the file door without
        inline extraction.

        ``background=True`` schedules the built items on the existing
        :class:`BackgroundIngestionQueue` instead of ingesting inline.
        Returns a summary dict with counts and per-file errors (capped at
        ``max_errors``).
        """
        summary: Dict[str, Any] = {
            "root": str(root_path),
            "recursive": bool(recursive),
            "background": bool(background),
            "scanned": 0,
            "matched": 0,
            "ingested": 0,
            "duplicate": 0,
            "failed": 0,
            "skipped": {"ignored": 0, "extension": 0, "too_large": 0, "hidden": 0},
            "truncated": False,
            "errors": [],
        }
        try:
            root = Path(root_path).expanduser()
        except TypeError:
            summary.update(status="failed", detail=f"invalid root path: {root_path!r}")
            return summary
        if not root.is_dir():
            summary.update(status="failed", detail=f"not a directory: {root}")
            return summary
        if not self.available():
            summary.update(
                status="unavailable",
                detail="Knowledge Graph is disabled (LATTICEAI_ENABLE_GRAPH).",
            )
            return summary
        summary["root"] = str(root)
        max_files = max(1, int(max_files))
        max_errors = max(0, int(max_errors))
        max_file_bytes = max(1, int(max_file_bytes))
        allowed_exts = (
            frozenset(str(e).lower() if str(e).startswith(".") else f".{str(e).lower()}" for e in extensions)
            if extensions
            else self._folder_extensions()
        )
        patterns = _load_latticeignore(root)
        errors: List[Dict[str, Any]] = summary["errors"]
        skipped = summary["skipped"]
        items: List[IngestionItem] = []

        def _record_error(path: Path, detail: str, status: str = "failed") -> None:
            summary["failed"] += 1
            if len(errors) < max_errors:
                errors.append({"path": str(path), "status": status, "detail": detail})

        for dirpath, dirnames, filenames in os.walk(root):
            current = Path(dirpath)
            rel_dir = current.relative_to(root)
            kept_dirs: List[str] = []
            for name in sorted(dirnames):
                if name in FOLDER_DEFAULT_SKIP_DIRS:
                    continue
                if name.startswith(".") and not include_hidden:
                    continue
                rel = name if str(rel_dir) == "." else (rel_dir / name).as_posix()
                if _matches_ignore(rel, name, is_dir=True, patterns=patterns):
                    skipped["ignored"] += 1
                    continue
                kept_dirs.append(name)
            dirnames[:] = kept_dirs if recursive else []

            for name in sorted(filenames):
                if name == LATTICEIGNORE_FILENAME:
                    continue
                summary["scanned"] += 1
                path = current / name
                rel = name if str(rel_dir) == "." else (rel_dir / name).as_posix()
                if name.startswith(".") and not include_hidden:
                    skipped["hidden"] += 1
                    continue
                if _matches_ignore(rel, name, is_dir=False, patterns=patterns):
                    skipped["ignored"] += 1
                    continue
                ext = path.suffix.lower()
                if ext not in allowed_exts:
                    skipped["extension"] += 1
                    continue
                try:
                    size = path.stat().st_size
                except OSError as exc:
                    _record_error(path, f"stat failed: {exc}")
                    continue
                if size > max_file_bytes:
                    skipped["too_large"] += 1
                    continue
                if len(items) >= max_files:
                    summary["truncated"] = True
                    break
                item_metadata: Dict[str, Any] = {"relative_path": rel}
                if ext in (FOLDER_MULTIMODAL_EXTENSIONS | FOLDER_VIDEO_EXTENSIONS) and self._allow_multimodal:
                    # Routed by modality inside ``ingest``; reading the bytes as
                    # UTF-8 here would only produce mojibake.
                    source_type = "file"
                elif ext in FOLDER_DOCUMENT_EXTENSIONS:
                    source_type = "pdf"
                else:
                    source_type = "file"
                    try:
                        content = path.read_text(encoding="utf-8", errors="ignore")
                    except OSError as exc:
                        _record_error(path, f"read failed: {exc}")
                        continue
                    item_metadata["extracted"] = {"content": content, "chars": len(content)}
                items.append(
                    IngestionItem(
                        source_type=source_type,
                        title=name,
                        path=str(path),
                        source_uri=str(path),
                        owner=owner,
                        workspace_id=workspace_id,
                        metadata=item_metadata,
                    )
                )
            if summary["truncated"]:
                break

        summary["matched"] = len(items)
        if background:
            job = self.schedule_background(
                items, incremental=True, user_email=user_email or owner,
            )
            summary.update(status="scheduled", job_id=job.job_id, scheduled=len(items))
            return summary

        for item in items:
            result = self.ingest(item, user_email=user_email or owner)
            if result.status == "ok":
                if result.duplicate:
                    summary["duplicate"] += 1
                else:
                    summary["ingested"] += 1
            else:
                _record_error(Path(item.path or ""), result.detail or result.status, result.status)
        summary["status"] = "ok" if summary["failed"] == 0 else "partial"
        return summary

    def _folder_extensions(self) -> frozenset:
        """Folder-scan allow-list — pictures, recordings and films when enabled.

        Video joins only when this machine can actually decode one, so a scan
        never fills the error list with files it was always going to refuse.
        """
        if not self._allow_multimodal:
            return DEFAULT_FOLDER_EXTENSIONS
        allowed = DEFAULT_FOLDER_EXTENSIONS | FOLDER_MULTIMODAL_EXTENSIONS
        if self._allow_video:
            return allowed | FOLDER_VIDEO_EXTENSIONS
        return allowed
