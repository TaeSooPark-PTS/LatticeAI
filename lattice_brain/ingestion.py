"""Unified ingestion pipeline — the single write-side seam into the Knowledge Graph.

v3.6.0 Knowledge Graph First principle: *no data source bypasses the Knowledge
Graph and no source creates an isolated silo*. Every source — local files,
connected folders, PDFs/Markdown/text/code, web URLs, browser tabs — is
normalized into one :class:`IngestionItem` and pushed through one
:meth:`IngestionPipeline.ingest` entrypoint:

    Source → normalize → content hash → (file | text) ingest → provenance

The pipeline is deliberately thin. It owns normalization, idempotency reporting,
provenance capture, and — crucially — routing every ingest through the shared
``dispatch_tool`` lifecycle so ``pre_tool``/``post_tool`` hooks fire on data
ingestion exactly as they do on tool calls. The heavy graph construction lives in
:class:`knowledge_graph.KnowledgeGraphStore` (``ingest_document`` for files,
``ingest_source`` for text/web), which this module composes rather than
re-implements.

Web ingestion seam
------------------
The graph layer never fetches or parses the web. Fetching, rendering,
readability extraction, and parse quality are the responsibility of the
*upstream* capture surfaces (browser extension, tools layer, MCP servers):
they hand this module already-extracted text. :meth:`IngestionPipeline.
ingest_web_page` is the convenience wrapper for that hand-off — it normalizes
``(url, extracted_text)`` into an ``IngestionItem(source_type="web_url")`` and
routes it through the exact same :meth:`IngestionPipeline.ingest` door as every
other source. If the extracted text is bad, fix the extractor upstream; the
pipeline will not attempt network access or HTML parsing.

Folder ingestion (:meth:`IngestionPipeline.ingest_folder`) walks a local
directory, honors a gitignore-like ``.latticeignore`` file at the root
(blank lines, ``#`` comments, ``fnmatch`` glob patterns, ``dir/`` suffix for
directories), always skips common noise (``.git``, ``node_modules``,
``__pycache__``, virtualenvs, ``dist``, hidden entries by default), applies
size/extension filters, and either ingests inline or schedules through the
existing :class:`BackgroundIngestionQueue`.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .runtime.hooks import dispatch_tool
from .utils import utc_now_iso

# Source types that arrive as a file on disk (read via ingest_document).
FILE_SOURCE_TYPES = frozenset({"file", "local_file", "upload", "pdf"})
# Source types that arrive as extracted text (read via ingest_source).
TEXT_SOURCE_TYPES = frozenset(
    {"web_url", "browser_tab", "text", "markdown", "note", "code", "clipboard"}
)
# Conversational exchanges (read via ingest_message — role/content semantics,
# conversation chaining). v4: chat and MCP messages stop bypassing the
# pipeline, so they carry provenance and fire the hook lifecycle like every
# other source.
CHAT_SOURCE_TYPES = frozenset({"chat_message", "mcp_message"})
# Typed memory records (read via ingest_event → Decision/Experience/Event
# nodes). The Memory System writes through the same door as everything else.
MEMORY_SOURCE_TYPES = frozenset({"decision", "experience", "workspace_event"})
_MEMORY_NODE_TYPES = {"decision": "Decision", "experience": "Experience", "workspace_event": "Event"}

DEFAULT_MAX_TEXT_BYTES = 5 * 1024 * 1024  # 5 MB of extracted text per item

# ── Folder ingestion (ingest_folder) filters ─────────────────────────────────
# Directories that are always pruned regardless of .latticeignore.
FOLDER_DEFAULT_SKIP_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "env",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".next",
        "target",
        ".cache",
        ".idea",
        ".vscode",
    }
)
# Extension filter matching FILE_SOURCE_TYPES conventions: text/markdown/code
# are read inline (extracted content → chunks); .pdf routes as source_type
# "pdf" through ingest_document (content extraction is upstream's concern).
FOLDER_TEXT_EXTENSIONS = frozenset(
    {".txt", ".md", ".markdown", ".rst", ".csv", ".json", ".yaml", ".yml", ".toml", ".ini"}
)
FOLDER_CODE_EXTENSIONS = frozenset(
    {
        ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".go", ".rs",
        ".java", ".c", ".h", ".cpp", ".hpp", ".rb", ".php", ".swift", ".kt",
        ".sh", ".sql",
    }
)
FOLDER_DOCUMENT_EXTENSIONS = frozenset({".pdf"})
DEFAULT_FOLDER_EXTENSIONS = (
    FOLDER_TEXT_EXTENSIONS | FOLDER_CODE_EXTENSIONS | FOLDER_DOCUMENT_EXTENSIONS
)
DEFAULT_MAX_FILE_BYTES = 4_000_000  # matches the local-index text/code budget
LATTICEIGNORE_FILENAME = ".latticeignore"
# Opt-out escape hatch for the post-ingest incremental vector sync.
AUTO_VECTOR_INDEX_ENV = "LATTICEAI_AUTO_VECTOR_INDEX"


def _load_latticeignore(root: Path) -> List[str]:
    """Parse ``root/.latticeignore`` → glob patterns (gitignore-like subset)."""
    ignore_file = root / LATTICEIGNORE_FILENAME
    patterns: List[str] = []
    if not ignore_file.is_file():
        return patterns
    try:
        lines = ignore_file.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return patterns
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line)
    return patterns


def _matches_ignore(
    rel_posix: str, name: str, *, is_dir: bool, patterns: Iterable[str]
) -> bool:
    """fnmatch-based .latticeignore matching.

    - ``pattern/`` matches directories only (files under it never appear
      because ignored directories are pruned during the walk).
    - Patterns match against both the root-relative posix path and the
      basename, so ``*.log`` and ``docs/draft.md`` both behave as expected.
    """
    for raw in patterns:
        pattern = raw
        if pattern.endswith("/"):
            if not is_dir:
                continue
            pattern = pattern.rstrip("/")
        pattern = pattern.lstrip("/")
        if not pattern:
            continue
        if fnmatch.fnmatch(rel_posix, pattern) or fnmatch.fnmatch(name, pattern):
            return True
    return False


# --- Large candidate 1 slice: incremental / background ingestion support ---
@dataclass
class BackgroundIngestionJob:
    """Job descriptor for background/incremental indexing (KG scale-up slice)."""
    job_id: str
    items: List[IngestionItem]
    status: str = "pending"  # pending | running | done | failed
    created_at: str = field(default_factory=utc_now_iso)
    processed: int = 0
    total: int = 0
    errors: List[str] = field(default_factory=list)


class BackgroundIngestionQueue:
    """Simple in-memory queue for background incremental ingestion.

    For large corpus: this is the seam where a real scheduler / worker pool
    (celery, rq, or internal thread) can be plugged later without changing callers.
    Supports incremental (skip duplicates) vs force reindex.
    """
    def __init__(self) -> None:
        self._jobs: Dict[str, BackgroundIngestionJob] = {}
        self._counter = 0

    def schedule(self, items: List[IngestionItem], *, incremental: bool = True) -> BackgroundIngestionJob:
        self._counter += 1
        job_id = f"bg_ingest_{self._counter:04d}"
        job = BackgroundIngestionJob(
            job_id=job_id,
            items=items,
            total=len(items),
        )
        # annotate items for downstream
        for it in job.items:
            # attach flag without breaking dataclass defaults (use metadata)
            it.metadata = {**it.metadata, "incremental": incremental, "bg_job": job_id}
        self._jobs[job_id] = job
        return job

    def get(self, job_id: str) -> Optional[BackgroundIngestionJob]:
        return self._jobs.get(job_id)

    def list_pending(self) -> List[BackgroundIngestionJob]:
        return [j for j in self._jobs.values() if j.status == "pending"]


@dataclass
class IngestionItem:
    """A single thing to ingest, normalized across every source type."""

    source_type: str
    title: Optional[str] = None
    text: Optional[str] = None          # text/web sources
    path: Optional[str] = None          # file sources
    source_uri: Optional[str] = None
    mime_type: Optional[str] = None
    owner: Optional[str] = None
    workspace_id: Optional[str] = None
    permissions: Optional[Dict[str, Any]] = None
    captured_at: Optional[str] = None
    modified_at: Optional[str] = None
    conversation_id: Optional[str] = None
    agent_used: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class IngestionResult:
    """The outcome of one ingestion, including provenance and idempotency."""

    status: str                         # ok | unavailable | blocked | failed
    source_type: str
    node_id: Optional[str] = None
    source_node_id: Optional[str] = None
    content_hash: Optional[str] = None
    title: Optional[str] = None
    chunk_ids: List[str] = field(default_factory=list)
    chunk_count: int = 0
    duplicate: bool = False
    embedded: bool = False
    indexing_status: str = "pending"    # indexed | skipped | failed | pending
    provenance_id: Optional[str] = None
    detail: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "source_type": self.source_type,
            "node_id": self.node_id,
            "source_node_id": self.source_node_id,
            "content_hash": self.content_hash,
            "title": self.title,
            "chunk_ids": self.chunk_ids,
            "chunk_count": self.chunk_count,
            "duplicate": self.duplicate,
            "embedded": self.embedded,
            "indexing_status": self.indexing_status,
            "provenance_id": self.provenance_id,
            "detail": self.detail,
        }


class IngestionPipeline:
    """Single normalized entrypoint that feeds every source into the graph."""

    def __init__(
        self,
        knowledge_graph: Any,
        *,
        hooks: Any = None,
        enable_graph: bool = True,
        audit: Optional[Any] = None,
        max_text_bytes: int = DEFAULT_MAX_TEXT_BYTES,
        pipeline_name: str = "unified-ingestion",
        bg_queue: Optional[BackgroundIngestionQueue] = None,
        auto_vector_index: bool = True,
    ) -> None:
        self._kg = knowledge_graph
        self._hooks = hooks
        self._enable = bool(enable_graph)
        self._audit = audit
        self._max_text_bytes = int(max_text_bytes)
        self._pipeline_name = pipeline_name
        self._bg_queue = bg_queue or BackgroundIngestionQueue()
        # Incremental vector sync after each successful non-duplicate ingest.
        # Constructor opt-out AND env opt-out (LATTICEAI_AUTO_VECTOR_INDEX=0)
        # both disable it; a vector failure never fails the ingest.
        env_flag = os.getenv(AUTO_VECTOR_INDEX_ENV, "1").strip().lower() not in {
            "0", "false", "no", "off",
        }
        self._auto_vector_index = bool(auto_vector_index) and env_flag

    def available(self) -> bool:
        return self._enable and self._kg is not None

    # ── public API ───────────────────────────────────────────────────────────
    def ingest(self, item: IngestionItem, *, user_email: Optional[str] = None) -> IngestionResult:
        """Normalize, hash, route through dispatch_tool, and record provenance."""
        source_type = str(item.source_type or "text").strip().lower()
        if not self.available():
            return IngestionResult(
                status="unavailable", source_type=source_type,
                indexing_status="skipped",
                detail="Knowledge Graph is disabled (LATTICEAI_ENABLE_GRAPH).",
            )

        captured_at = item.captured_at or utc_now_iso()
        owner = item.owner or user_email
        tool_name = f"kg_ingest.{source_type}"
        # Only the keys are read by the hook payload, so this dict is safe/cheap.
        args = {
            "source_type": source_type,
            "source_uri": item.source_uri,
            "owner": owner,
            "workspace_id": item.workspace_id,
        }

        def _run() -> Dict[str, Any]:
            if source_type in CHAT_SOURCE_TYPES:
                return self._ingest_chat(item, source_type=source_type, owner=owner)
            if source_type in MEMORY_SOURCE_TYPES:
                return self._ingest_memory_record(item, source_type=source_type, owner=owner)
            if source_type in FILE_SOURCE_TYPES or (item.path and not item.text):
                return self._ingest_file(item, source_type=source_type, owner=owner, captured_at=captured_at)
            return self._ingest_text(item, source_type=source_type, owner=owner, captured_at=captured_at)

        try:
            raw = dispatch_tool(
                self._hooks, tool_name, args, _run,
                user_email=user_email, workspace_id=item.workspace_id, source="ingestion",
            )
        except PermissionError as exc:
            return IngestionResult(
                status="blocked", source_type=source_type,
                indexing_status="skipped", detail=str(exc),
            )
        except FileNotFoundError as exc:
            return IngestionResult(
                status="failed", source_type=source_type,
                indexing_status="failed", detail=str(exc),
            )
        except Exception as exc:  # noqa: BLE001 — surface as a failed result, never crash the caller
            return IngestionResult(
                status="failed", source_type=source_type,
                indexing_status="failed", detail=str(exc),
            )

        node_id = raw.get("node_id")
        content_hash = raw.get("content_hash") or raw.get("sha256")
        chunk_ids = list(raw.get("chunk_ids") or [])
        title = raw.get("title") or item.title

        # Incremental vector-index sync (opt-in via auto_vector_index +
        # LATTICEAI_AUTO_VECTOR_INDEX). Exception-safe by contract: the graph
        # write above already landed, so a vector failure only downgrades
        # indexing_status to "pending" — index_status()/rebuild_vector_index()
        # discover the same node as backlog and pick it up later.
        indexing_status = "indexed"
        vector_detail: Optional[str] = None
        if node_id and self._auto_vector_index and not bool(raw.get("duplicate")):
            indexing_status, vector_detail = self._sync_vector_index(node_id)
        embedded = bool(self._kg.node_is_embedded(node_id)) if node_id else False

        # Provenance capture must never turn an already-persisted ingest into a
        # caller-visible failure: the graph write above succeeded, so a broken
        # provenance table degrades the result instead of raising.
        provenance_detail: Optional[str] = None
        try:
            prov = self._kg.record_provenance(
                node_id=node_id,
                source_type=source_type,
                pipeline=self._pipeline_name,
                source_uri=item.source_uri,
                content_hash=content_hash,
                title=title,
                owner=owner,
                workspace_id=item.workspace_id,
                captured_at=captured_at,
                modified_at=item.modified_at,
                embedded=embedded,
                linked=bool(raw.get("source_node_id")),
                duplicate=bool(raw.get("duplicate")),
                agent_used=item.agent_used,
                chunk_count=len(chunk_ids),
                permissions=item.permissions,
                metadata=item.metadata,
            )
        except Exception as exc:  # noqa: BLE001 — the ingest itself already landed
            prov = {}
            provenance_detail = f"provenance capture failed: {exc}"
        if self._audit is not None:
            try:
                self._audit(
                    "kg_ingest",
                    {
                        "source_type": source_type, "node_id": node_id,
                        "content_hash": content_hash, "duplicate": bool(raw.get("duplicate")),
                    },
                    user_email,
                )
            except Exception:  # noqa: BLE001 — audit must never break ingestion
                pass

        details = [d for d in (provenance_detail, vector_detail) if d]
        return IngestionResult(
            status="ok",
            source_type=source_type,
            node_id=node_id,
            source_node_id=raw.get("source_node_id"),
            content_hash=content_hash,
            title=title,
            chunk_ids=chunk_ids,
            chunk_count=len(chunk_ids),
            duplicate=bool(raw.get("duplicate")),
            embedded=embedded,
            indexing_status=indexing_status,
            provenance_id=prov.get("id"),
            detail="; ".join(details) if details else None,
        )

    def _sync_vector_index(self, node_id: str) -> Tuple[str, Optional[str]]:
        """Best-effort incremental vector sync → (indexing_status, detail).

        Any failure — missing method on older stores, embedding provider down,
        storage error — yields ``("pending", detail)`` so a later
        ``rebuild_vector_index`` run picks the node up from the backlog.
        """
        sync = getattr(self._kg, "index_node_incremental", None)
        if not callable(sync):
            # Older store without the incremental path: the write-side already
            # embeds inline, so nothing extra to do.
            return "indexed", None
        try:
            outcome = sync(node_id) or {}
        except Exception as exc:  # noqa: BLE001 — vector sync must never fail the ingest
            return "pending", f"vector index sync failed: {exc}"
        if str(outcome.get("status") or "") == "failed":
            reason = outcome.get("detail") or "unknown error"
            return "pending", f"vector index sync failed: {reason}"
        return "indexed", None

    # --- Large candidate #1: background / incremental scheduling (slice) ---
    def schedule_background(
        self,
        items: List[IngestionItem],
        *,
        incremental: bool = True,
    ) -> BackgroundIngestionJob:
        """Schedule items for background incremental indexing.

        Returns a job handle. Actual execution can be driven by caller
        (or future worker) calling pipeline.ingest on each. This seam enables
        large-corpus scale without blocking user requests.
        """
        job = self._bg_queue.schedule(items, incremental=incremental)
        # mark initial status on results concept (jobs track)
        return job

    def get_background_job(self, job_id: str) -> Optional[BackgroundIngestionJob]:
        return self._bg_queue.get(job_id)

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
            else DEFAULT_FOLDER_EXTENSIONS
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
                if ext in FOLDER_DOCUMENT_EXTENSIONS:
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
            job = self.schedule_background(items, incremental=True)
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

    # ── routing helpers ──────────────────────────────────────────────────────
    def _ingest_text(self, item, *, source_type, owner, captured_at) -> Dict[str, Any]:
        text = item.text or ""
        if not text.strip():
            raise ValueError(
                f"Empty content: {source_type} ingestion requires non-empty text."
            )
        if len(text.encode("utf-8", "ignore")) > self._max_text_bytes:
            raise ValueError(
                f"Text payload exceeds the {self._max_text_bytes // (1024 * 1024)}MB ingestion limit."
            )
        title = item.title or item.source_uri or source_type
        return self._kg.ingest_source(
            source_type=source_type,
            title=title,
            text=text,
            source_uri=item.source_uri,
            owner=owner,
            workspace_id=item.workspace_id,
            permissions=item.permissions,
            captured_at=captured_at,
            modified_at=item.modified_at,
            conversation_id=item.conversation_id,
            metadata={"mime_type": item.mime_type, **(item.metadata or {})},
        )

    def _ingest_chat(self, item, *, source_type, owner) -> Dict[str, Any]:
        text = item.text or ""
        meta = item.metadata or {}
        role = str(meta.get("role") or "user")
        result = self._kg.ingest_message(
            role,
            text,
            user_email=owner,
            user_nickname=meta.get("user_nickname"),
            source=meta.get("source") or source_type,
            conversation_id=item.conversation_id,
            workspace_id=item.workspace_id,
            raw=meta.get("raw"),
        )
        # ingest_message reports message/response node ids; normalize the keys
        # the provenance step expects.
        result.setdefault("node_id", result.get("node_id") or result.get("message_node_id") or result.get("id"))
        result.setdefault("title", item.title or text[:80])
        return result

    def _ingest_memory_record(self, item, *, source_type, owner) -> Dict[str, Any]:
        node_type = _MEMORY_NODE_TYPES[source_type]
        meta = item.metadata or {}
        result = self._kg.ingest_event(
            node_type,
            item.title or (item.text or node_type)[:120],
            user_email=owner,
            source=meta.get("source") or source_type,
            conversation_id=item.conversation_id,
            workspace_id=item.workspace_id,
            metadata={**meta, "detail": (item.text or "")[:2000]},
        )
        result.setdefault("node_id", result.get("node_id") or result.get("id"))
        result.setdefault("title", item.title)
        return result

    def _ingest_file(self, item, *, source_type, owner, captured_at) -> Dict[str, Any]:
        if not item.path:
            raise ValueError("File ingestion requires a path.")
        path = Path(item.path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        if path.is_dir():
            raise ValueError(f"File ingestion requires a file, got a directory: {path}")
        return self._kg.ingest_document(
            path,
            original_filename=item.title or path.name,
            mime_type=item.mime_type,
            uploader=owner,
            conversation_id=item.conversation_id,
            extracted=item.metadata.get("extracted") if item.metadata else None,
            source_type=source_type,
            source_uri=item.source_uri or str(path),
            captured_at=captured_at,
            modified_at=item.modified_at,
            owner=owner,
            workspace_id=item.workspace_id,
            permissions=item.permissions,
        )


def content_hash_text(text: str) -> str:
    """Canonical content hash for a text payload (matches store hashing scheme)."""
    return hashlib.sha256((text or "").encode("utf-8", "ignore")).hexdigest()
