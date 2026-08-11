"""The single normalized entrypoint every source is fed through.

    Source → normalize → content hash → (file | text) ingest → provenance

:meth:`IngestionPipeline.ingest` owns normalization, the gate reads, idempotency
reporting, provenance capture, the advisory quality annotation, and routing
every ingest through the shared ``dispatch_tool`` lifecycle so ``pre_tool`` /
``post_tool`` hooks fire on data ingestion exactly as they do on tool calls.
The per-source doors live in ``routing.py``, the folder walk in ``folders.py``
and background scheduling in ``jobs_api.py``; this module is what they compose
into, and it owns the constructor state all three read.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..graph.vector_index import DEFAULT_TICK_LIMIT as VECTOR_TICK_LIMIT
from ..ingestion_jobs import BackgroundIngestionQueue
from ..multimodal import (
    DEFAULT_KEYFRAMES,
    MODALITY_AUDIO,
    MODALITY_IMAGE,
    MODALITY_VIDEO,
    VIDEO_UNAVAILABLE_DETAIL,
    MultimodalPorts,
    ffmpeg_available,
)
from ..quiet import quiet
from ..runtime.hooks import dispatch_tool
from ..utils import utc_now_iso
from .constants import (
    ALLOW_MULTIMODAL_ENV,
    ALLOW_VIDEO_ENV,
    AUTO_VECTOR_INDEX_GATE,
    CHAT_SOURCE_TYPES,
    DEFAULT_MAX_TEXT_BYTES,
    FILE_SOURCE_TYPES,
    MEMORY_SOURCE_TYPES,
    MULTIMODAL_GATE,
    VIDEO_GATE,
)
from .folders import IngestionFolderMixin
from .jobs_api import IngestionJobsMixin
from .models import IngestionItem, IngestionResult
from .quality import QUALITY_LOW_WARNING, assess_extraction_quality
from .routing import IngestionRoutingMixin


class IngestionPipeline(
    IngestionRoutingMixin, IngestionFolderMixin, IngestionJobsMixin
):
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
        allow_multimodal: bool = False,
        multimodal: Optional[MultimodalPorts] = None,
    ) -> None:
        self._kg = knowledge_graph
        self._hooks = hooks
        self._enable = bool(enable_graph)
        self._audit = audit
        self._max_text_bytes = int(max_text_bytes)
        self._pipeline_name = pipeline_name
        # Background job state lives in the graph database by default, so a
        # restart resumes from the last completed item instead of replaying the
        # whole corpus. A store without a usable ``db_path`` (mocks, disabled
        # graph) degrades to the historical in-memory queue, which reports
        # itself as non-durable through ``BackgroundIngestionQueue.describe()``.
        self._bg_queue = bg_queue or BackgroundIngestionQueue(
            db_path=getattr(knowledge_graph, "db_path", None)
        )
        # Incremental vector sync after each successful non-duplicate ingest.
        # Constructor opt-out AND gate opt-out (LATTICEAI_AUTO_VECTOR_INDEX=0,
        # or the settings toggle bound to it) both disable it; a vector failure
        # never fails the ingest. The gate half is asked per ingest rather than
        # frozen here, so turning it off takes effect on the next item.
        self._auto_vector_index_opt_in = bool(auto_vector_index)
        # Multi-modal routing. Off unless the caller asks for it *or* the gate
        # says yes — the env behind that gate is the escape hatch for an
        # install with no code path to the constructor (CLI, background
        # worker), and the gate is now asked per call so a runtime toggle can
        # reach it. A constructor ``True`` is still a permanent yes.
        self._multimodal_opt_in = bool(allow_multimodal)
        self._multimodal = multimodal or MultimodalPorts()
        self._keyframes = DEFAULT_KEYFRAMES

    @property
    def _auto_vector_index(self) -> bool:
        """Whether a landed ingest also syncs its vector, asked *now*."""
        return self._auto_vector_index_opt_in and AUTO_VECTOR_INDEX_GATE.enabled()

    @property
    def _allow_multimodal(self) -> bool:
        """Whether pictures and recordings route by modality, asked *now*."""
        return self._multimodal_opt_in or MULTIMODAL_GATE.enabled()

    @property
    def _allow_video(self) -> bool:
        """Video needs multi-modal on, its own sub-switch on, and a decoder."""
        return self._allow_multimodal and VIDEO_GATE.enabled() and self._can_decode_video()

    def _can_decode_video(self) -> bool:
        """An injected keyframe port counts as a decoder; otherwise, ffmpeg."""
        return self._multimodal.keyframe_extractor is not None or ffmpeg_available()

    def available(self) -> bool:
        return self._enable and self._kg is not None

    def multimodal_status(self) -> Dict[str, Any]:
        """What this pipeline will do with a picture or a recording, honestly.

        ``enabled`` is the flag; the rest is which model-backed capabilities
        were actually injected. Video reports whether it can really run — the
        answer is no on a machine with no ffmpeg, and it says which of the two
        reasons applies rather than leaving the surface to guess.
        """
        allowed = self._allow_multimodal
        video = self._allow_video
        return {
            "enabled": allowed,
            "image": allowed,
            "audio": allowed,
            "video": video,
            "video_detail": None if video else self._video_refusal(),
            "gates": {
                "multimodal": MULTIMODAL_GATE.describe(),
                "video": VIDEO_GATE.describe(),
            },
            **self._multimodal.describe(),
        }

    def _video_refusal(self) -> str:
        """Why a video would be refused right now — never a stale reason."""
        if not self._allow_multimodal:
            return (
                "multi-modal ingestion is off; pictures, recordings and videos "
                f"are only stored when {ALLOW_MULTIMODAL_ENV} is on"
            )
        if not VIDEO_GATE.enabled():
            return (
                "video ingestion is turned off for this install "
                f"({ALLOW_VIDEO_ENV}); pictures and recordings are unaffected"
            )
        return VIDEO_UNAVAILABLE_DETAIL

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

        # Modality routing is a no-op while the flag is off: ``modality`` stays
        # "text" and every branch below behaves exactly as it did before.
        modality = self._modality_for(item, source_type)
        if modality == MODALITY_VIDEO and not self._allow_video:
            # Recognized and refused, with the reason that actually applies
            # right now — a missing decoder is not the same answer as a
            # switched-off feature, and the caller can act on the difference.
            return IngestionResult(
                status="unavailable", source_type=source_type,
                indexing_status="skipped", detail=self._video_refusal(),
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
            if modality == MODALITY_IMAGE:
                return self._ingest_image(item, source_type=source_type, owner=owner, captured_at=captured_at)
            if modality == MODALITY_AUDIO:
                return self._ingest_audio(item, source_type=source_type, owner=owner, captured_at=captured_at)
            if modality == MODALITY_VIDEO:
                return self._ingest_video(item, source_type=source_type, owner=owner, captured_at=captured_at)
            if source_type in FILE_SOURCE_TYPES or (item.path and not item.text):
                return self._ingest_file(item, source_type=source_type, owner=owner, captured_at=captured_at)
            return self._ingest_text(item, source_type=source_type, owner=owner, captured_at=captured_at)

        # v9.8.0 observation-only quality gate: computed *before* the write so
        # the search never matches the node we are about to create. It is
        # recorded on the result and never skips an ingest (behavior unchanged).
        quality_text = self._extractable_text(item)
        quality_gate = self._observe_quality_gate(
            item, source_type=source_type, text=quality_text,
        )

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
                quiet()

        # A modality-aware door scores its own extraction (a picture's quality
        # is "how much of it can be retrieved", not "does the text read well"),
        # so its verdict wins. Text/file doors never set the key and keep the
        # historical scoring untouched.
        extraction_quality = raw.get("extraction_quality") or self._assess_item_quality(
            item, source_type=source_type, text=quality_text, chunk_ids=chunk_ids,
        )
        warnings: List[str] = []
        if extraction_quality is not None and extraction_quality.get("level") == "low":
            warnings.append(QUALITY_LOW_WARNING)

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
            extraction_quality=extraction_quality,
            warnings=warnings,
            quality_gate=quality_gate,
        )

    # ── extraction quality (v9.8.0 A1 — advisory, never gates) ───────────────
    @staticmethod
    def _extractable_text(item: IngestionItem) -> Optional[str]:
        """Best available extracted text for quality scoring/gating."""
        if item.text is not None:
            return item.text
        extracted = (item.metadata or {}).get("extracted")
        if isinstance(extracted, dict):
            content = extracted.get("content") or extracted.get("text")
            if content is not None:
                return str(content)
        return None

    @staticmethod
    def _upstream_confidence(item: IngestionItem) -> Optional[Any]:
        """Upstream extractor confidence, if the capture surface supplied one."""
        meta = item.metadata or {}
        extracted = meta.get("extracted")
        if isinstance(extracted, dict) and extracted.get("confidence") is not None:
            return extracted.get("confidence")
        if meta.get("extraction_confidence") is not None:
            return meta.get("extraction_confidence")
        return None

    def _assess_item_quality(
        self,
        item: IngestionItem,
        *,
        source_type: str,
        text: Optional[str],
        chunk_ids: List[str],
    ) -> Optional[Dict[str, Any]]:
        """Quality annotation for document-like sources (not chat/memory)."""
        if source_type in CHAT_SOURCE_TYPES or source_type in MEMORY_SOURCE_TYPES:
            return None
        confidence = self._upstream_confidence(item)
        if text is not None or confidence is not None:
            return assess_extraction_quality(
                text, source_type=source_type, upstream_confidence=confidence,
            )
        # File door without inline extraction (e.g. PDF): the pipeline never saw
        # the text, so score honestly from the chunk output instead of guessing.
        if chunk_ids:
            return {
                "score": 0.5,
                "level": "medium",
                "reasons": ["content_extracted_upstream_not_scored"],
            }
        return {"score": 0.0, "level": "low", "reasons": ["no_extracted_text"]}

    def _observe_quality_gate(
        self,
        item: IngestionItem,
        *,
        source_type: str,
        text: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        """Observation-mode ``gate_ingest_candidate`` wiring.

        Records what the proactive gate *would* decide (ingest /
        skip_duplicate / review) without ever acting on it. Any failure —
        import, search, gate — yields ``None``; the ingest proceeds untouched.
        """
        if source_type in CHAT_SOURCE_TYPES or source_type in MEMORY_SOURCE_TYPES:
            return None
        body = str(text or "").strip()
        if not body:
            return None
        try:
            from ..graph.proactive import gate_ingest_candidate
        except Exception:  # noqa: BLE001 — optional observation, never required
            return None

        def _search(query: str) -> Any:
            snippet = str(query or "")[:400]
            try:
                if item.workspace_id:
                    return self._kg.search(
                        snippet, 20, allowed_workspaces={item.workspace_id},
                    )
                return self._kg.search(snippet, 20)
            except TypeError:
                # Older store without workspace-scoped search.
                return self._kg.search(snippet, 20)

        try:
            gate = gate_ingest_candidate(body, _search)
        except Exception:  # noqa: BLE001 — observation must never fail the ingest
            return None
        parts = [str(gate.get("reason") or "")]
        if gate.get("similarity") is not None:
            parts.append(f"similarity={gate.get('similarity')}")
        if gate.get("match_id"):
            parts.append(f"match={gate.get('match_id')}")
        return {
            "action": str(gate.get("action") or "review"),
            "detail": "; ".join(p for p in parts if p),
        }

    def _queue_pending_embed(self, node_id: str, detail: str) -> bool:
        """Hand a node the inline sync could not embed to the background queue.

        Before v11.1.0 ``indexing_status="pending"`` was the end of the story:
        honest, but nobody was coming back for it, so the node stayed
        unsearchable until a human ran a rebuild. The durable queue is who
        comes back. A store without one (older stores, mocks) just keeps the
        old behaviour — the node is still visible as ``index_status`` backlog.
        """
        queue = getattr(self._kg, "vector_queue", None)
        if queue is None:
            return False
        try:
            return bool(queue.schedule(node_id, detail=detail))
        except Exception:  # noqa: BLE001 — queueing must never fail an ingest
            quiet()
            return False

    def _sync_vector_index(self, node_id: str) -> Tuple[str, Optional[str]]:
        """Best-effort incremental vector sync → (indexing_status, detail).

        Any failure — missing method on older stores, embedding provider down,
        storage error — yields ``("pending", detail)`` so a later
        ``rebuild_vector_index`` run picks the node up from the backlog, and
        the node is queued for background embedding so that pickup happens on
        its own.
        """
        sync = getattr(self._kg, "index_node_incremental", None)
        if not callable(sync):
            # Older store without the incremental path: the write-side already
            # embeds inline, so nothing extra to do.
            return "indexed", None
        try:
            outcome = sync(node_id) or {}
        except Exception as exc:  # noqa: BLE001 — vector sync must never fail the ingest
            return "pending", self._pending_detail(node_id, f"vector index sync failed: {exc}")
        if str(outcome.get("status") or "") == "failed":
            reason = outcome.get("detail") or "unknown error"
            return "pending", self._pending_detail(
                node_id, f"vector index sync failed: {reason}"
            )
        return "indexed", None

    def _pending_detail(self, node_id: str, reason: str) -> str:
        """``reason``, plus whether a background retry was actually scheduled."""
        if self._queue_pending_embed(node_id, reason):
            return f"{reason}; queued for background embedding"
        return reason

    def drain_vector_queue(self, limit: int = VECTOR_TICK_LIMIT) -> Dict[str, Any]:
        """Run one background-embedding tick over the store's pending backlog.

        Deliberately caller-driven (a scheduler, a CLI, a test) rather than a
        thread this pipeline owns: the queue is durable, so "who runs it" is a
        deployment decision, not a property of having ingested something.
        """
        queue = getattr(self._kg, "vector_queue", None)
        if queue is None:
            return {
                "claimed": 0,
                "indexed": 0,
                "retried": 0,
                "failed": 0,
                "detail": "this store has no background vector queue",
            }
        return dict(queue.tick(limit))
