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
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

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
    ) -> None:
        self._kg = knowledge_graph
        self._hooks = hooks
        self._enable = bool(enable_graph)
        self._audit = audit
        self._max_text_bytes = int(max_text_bytes)
        self._pipeline_name = pipeline_name
        self._bg_queue = bg_queue or BackgroundIngestionQueue()

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
        embedded = bool(self._kg.node_is_embedded(node_id)) if node_id else False
        title = raw.get("title") or item.title

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
            indexing_status="indexed",
            provenance_id=prov.get("id"),
            detail=provenance_detail,
        )

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
