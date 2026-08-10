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

from .gates import FeatureGate
from .graph.vector_index import DEFAULT_TICK_LIMIT as VECTOR_TICK_LIMIT
from .multimodal import (
    AUDIO_EXTENSIONS,
    DEFAULT_KEYFRAMES,
    IMAGE_EXTENSIONS,
    MODALITY_AUDIO,
    MODALITY_IMAGE,
    MODALITY_VIDEO,
    VIDEO_EXTENSIONS,
    VIDEO_UNAVAILABLE_DETAIL,
    ImageFacts,
    MultimodalPorts,
    audio_quality_score,
    detect_modality,
    extract_image_facts,
    ffmpeg_available,
    image_quality_score,
    read_video_facts,
    transcribe_audio,
    video_frame_dir,
    video_quality_score,
    write_image_memory,
    write_video_memory,
)
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
#: Default *on*, unlike every other gate here: new material has always been made
#: searchable straight away, and this exists so a settings surface can turn that
#: off (batch reindex later) without a restart. ``FeatureGate`` parses the env
#: var with the same words the hand-written opt-out check used, so an untouched
#: install — including one with a nonsense value — answers exactly as before.
AUTO_VECTOR_INDEX_GATE = FeatureGate(
    AUTO_VECTOR_INDEX_ENV,
    default=True,
    name="auto_vector_index",
    detail="New material is prepared for semantic search as soon as it lands.",
)

# ── Multi-modal ingestion (v11.1.0 Track 3) ──────────────────────────────────
# Opt-in, default off, on purpose. Turning it on changes what a folder scan
# *stores* (pictures and recordings, with OCR and — if a model is loaded —
# captions and vectors), and that is the user's call, not a default. With the
# flag off every routing decision below is skipped and behaviour is byte-for-
# byte what it was before this release.
ALLOW_MULTIMODAL_ENV = "LATTICEAI_ALLOW_MULTIMODAL"
#: The multi-modal switch, resolved when it is asked rather than frozen into
#: ``self`` at construction (v11.2.0). The environment variable is still the
#: answer for an untouched install — same var, same words, same default off —
#: but a settings surface can bind a resolver and move it without a restart.
MULTIMODAL_GATE = FeatureGate(
    ALLOW_MULTIMODAL_ENV,
    default=False,
    name="allow_multimodal",
    detail="Pictures and recordings are only ingested when this is turned on.",
)
#: Video is a *sub-switch* of the one above: with multi-modal off nothing about
#: video happens at all, and with it on video is included unless this is
#: explicitly turned off. The effective default is therefore still "no video",
#: and the seam exists so a settings screen can offer pictures without films.
ALLOW_VIDEO_ENV = "LATTICEAI_ALLOW_VIDEO"
VIDEO_GATE = FeatureGate(
    ALLOW_VIDEO_ENV,
    default=True,
    name="allow_video",
    detail="Videos are ingested as keyframes plus subtitles when multi-modal is on.",
)
#: Source types that name a modality outright (a caller who already knows).
IMAGE_SOURCE_TYPES = frozenset({"image", "screenshot", "photo"})
AUDIO_SOURCE_TYPES = frozenset({"audio", "voice_memo", "recording"})
VIDEO_SOURCE_TYPES = frozenset({"video", "screen_recording", "movie"})
#: Added to the folder-scan allow-list only while multimodal is enabled.
FOLDER_MULTIMODAL_EXTENSIONS = IMAGE_EXTENSIONS | AUDIO_EXTENSIONS
#: Videos join the folder allow-list only when this machine can decode one —
#: scanning a folder into a pile of refusals is not a feature.
FOLDER_VIDEO_EXTENSIONS = VIDEO_EXTENSIONS
#: Graph node type for a recording. ``NodeType.AUDIO`` normalizes this on the
#: KG v2 write side; the legacy tables keep the label verbatim, which is what
#: every type-aware read (graph view, context sections, doc-gen) matches on.
AUDIO_NODE_TYPE = "Audio"

# ── Extraction quality heuristics (v9.8.0 A1) ────────────────────────────────
# Pure heuristics over the extracted text — no model calls, no network. The
# score is *advisory*: it never blocks an ingest, it only annotates the result
# so capture surfaces (browser, folder scan) can surface low-quality warnings.
QUALITY_HIGH_THRESHOLD = 0.7
QUALITY_LOW_THRESHOLD = 0.4
QUALITY_LOW_WARNING = "추출 품질이 낮습니다 — 원문 확인을 권장합니다."
_WEB_SOURCE_TYPES = frozenset({"web_url", "browser_tab"})
# Standalone short lines that smell like leftover site chrome (nav/menu/footer).
_BOILERPLATE_LINE_MARKERS = frozenset(
    {
        "home", "menu", "nav", "navigation", "login", "log in", "sign in",
        "sign up", "register", "subscribe", "search", "about", "about us",
        "contact", "contact us", "privacy policy", "terms of service",
        "cookie policy", "accept cookies", "accept all cookies", "share",
        "skip to content", "copyright", "all rights reserved", "sitemap",
        "back to top", "footer", "read more", "next", "previous",
    }
)


def _quality_level(score: float) -> str:
    if score >= QUALITY_HIGH_THRESHOLD:
        return "high"
    if score >= QUALITY_LOW_THRESHOLD:
        return "medium"
    return "low"


def assess_extraction_quality(
    text: Optional[str],
    *,
    source_type: Optional[str] = None,
    upstream_confidence: Optional[Any] = None,
) -> Dict[str, Any]:
    """Score extracted text 0..1 with reasons (pure heuristic, deterministic).

    Signals: text length, whitespace ratio, character/word diversity
    (repetition), sentence structure, and — for web sources — leftover
    nav/menu boilerplate. When the upstream extractor supplies its own
    confidence (``upstream_confidence``), that value wins verbatim: the
    extractor saw the raw document, this function only sees its output.
    """
    if upstream_confidence is not None:
        try:
            score = max(0.0, min(1.0, float(upstream_confidence)))
        except (TypeError, ValueError):
            score = None
        if score is not None:
            return {
                "score": round(score, 4),
                "level": _quality_level(score),
                "reasons": ["upstream_confidence"],
            }

    raw = str(text or "")
    stripped = raw.strip()
    if not stripped:
        return {"score": 0.0, "level": "low", "reasons": ["empty_text"]}

    reasons: List[str] = []
    length = len(stripped)
    sample = stripped[:4000]
    lines = [ln.strip() for ln in stripped.splitlines() if ln.strip()]
    words = stripped.split()

    # 1) Length — very short extractions rarely carry recall value.
    if length < 40:
        length_factor = 0.35
        reasons.append("very_short_text")
    elif length < 120:
        length_factor = 0.6
        reasons.append("short_text")
    elif length < 300:
        length_factor = 0.85
    else:
        length_factor = 1.0

    # 2) Sentence structure — prose has sentence-ending punctuation.
    sentence_marks = sum(sample.count(mark) for mark in (".", "!", "?", "…", "。", "！", "？"))
    if sentence_marks > 0:
        structure_factor = 1.0
    elif length < 200:
        structure_factor = 0.75  # titles/snippets legitimately lack periods
    else:
        structure_factor = 0.45
        reasons.append("no_sentence_structure")

    # 3) Diversity — repeated characters/lines/words indicate extraction junk.
    diversity_factor = 1.0
    distinct_chars = len(set(sample.lower()))
    if distinct_chars < 10:
        diversity_factor *= 0.2
        reasons.append("low_character_diversity")
    elif distinct_chars < 20:
        diversity_factor *= 0.7
    if len(lines) >= 6:
        top_count = max(lines.count(ln) for ln in set(lines))
        if top_count >= max(3, len(lines) // 4):
            diversity_factor *= 0.5
            reasons.append("repetitive_lines")
    if len(words) >= 30 and (len(set(w.lower() for w in words)) / len(words)) < 0.25:
        diversity_factor *= 0.5
        reasons.append("repetitive_words")

    # 4) Cleanliness — whitespace floods, fragmented lines, site chrome.
    cleanliness_factor = 1.0
    whitespace_ratio = sum(1 for ch in raw if ch.isspace()) / max(1, len(raw))
    if whitespace_ratio > 0.45:
        cleanliness_factor *= 0.6
        reasons.append("high_whitespace_ratio")
    if len(lines) >= 8:
        short_lines = sum(1 for ln in lines if len(ln.split()) <= 3)
        if short_lines / len(lines) > 0.6:
            cleanliness_factor *= 0.6
            reasons.append("fragmented_lines")
    boilerplate_hits = sum(
        1 for ln in lines if ln.lower().strip(" .:>|•·-–—*") in _BOILERPLATE_LINE_MARKERS
    )
    if lines and boilerplate_hits >= 3 and (boilerplate_hits / len(lines)) > 0.2:
        cleanliness_factor *= 0.35
        if str(source_type or "").lower() in _WEB_SOURCE_TYPES:
            reasons.append("nav_menu_remnants")
        else:
            reasons.append("boilerplate_markers")

    score = length_factor * structure_factor * diversity_factor * cleanliness_factor
    score = max(0.0, min(1.0, score))
    if not reasons:
        reasons.append("clean_extraction")
    return {"score": round(score, 4), "level": _quality_level(score), "reasons": reasons}


# ── capture quality CTA (backlog #9, review §7.2 C) ──────────────────────────
# Structured verdict over the same extraction-quality schema the rest of the
# pipeline uses, so capture surfaces (browser extension, read-url) can render
# an honest "this capture is thin" CTA instead of silently storing junk.
CAPTURE_SUGGESTIONS_THIN = ["recapture", "paste_manually", "highlight_source"]
_CAPTURE_REASON_LABELS = {
    "empty_text": "추출된 본문이 비어 있습니다",
    "very_short_text": "추출된 본문이 매우 짧습니다",
    "short_text": "추출된 본문이 짧습니다",
    "no_sentence_structure": "문장 구조가 거의 없습니다",
    "low_character_diversity": "반복 문자가 대부분입니다",
    "repetitive_lines": "같은 줄이 반복됩니다",
    "repetitive_words": "같은 단어가 반복됩니다",
    "high_whitespace_ratio": "공백이 지나치게 많습니다",
    "fragmented_lines": "줄이 잘게 조각나 있습니다",
    "nav_menu_remnants": "메뉴/내비게이션 잔여물이 많습니다",
    "boilerplate_markers": "상용구 텍스트가 많습니다",
    "no_extracted_text": "추출된 텍스트가 없습니다",
}


def capture_quality_verdict(
    extraction_quality: Optional[Dict[str, Any]],
    *,
    source_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Structured CTA verdict from a pipeline ``extraction_quality`` dict.

    ``{"status": "thin"|"ok", "reason": str|None, "suggestions": [...],
    "score": float|None, "level": str|None}``. ``thin`` (level == "low", the
    same threshold as the ingest warning) carries actionable suggestions —
    ``recapture`` / ``paste_manually`` / ``highlight_source`` — so the UI can
    offer the user a way to fix the capture instead of hiding the problem.
    Deterministic and never raises; ``None`` input yields an honest ``thin``.
    """
    if not isinstance(extraction_quality, dict):
        return {
            "status": "thin",
            "reason": _CAPTURE_REASON_LABELS["no_extracted_text"],
            "reason_codes": ["no_extracted_text"],
            "suggestions": list(CAPTURE_SUGGESTIONS_THIN),
            "score": None,
            "level": None,
        }
    level = str(extraction_quality.get("level") or "")
    score = extraction_quality.get("score")
    reasons = [str(item) for item in (extraction_quality.get("reasons") or [])]
    thin = level == "low"
    reason = None
    if thin:
        labeled = [
            _CAPTURE_REASON_LABELS[code]
            for code in reasons
            if code in _CAPTURE_REASON_LABELS
        ]
        reason = "; ".join(labeled) if labeled else QUALITY_LOW_WARNING
    return {
        "status": "thin" if thin else "ok",
        "reason": reason,
        "reason_codes": reasons if thin else [],
        "suggestions": list(CAPTURE_SUGGESTIONS_THIN) if thin else [],
        "score": score,
        "level": level or None,
    }


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


# Background job scheduling + progress lives in its own module (v9.9.6):
# the pipeline owns "ingest one item", the queue owns "schedule many and
# report progress". Re-exported so every existing import keeps working.
from .ingestion_jobs import (  # noqa: E402,F401 — re-export for existing importers
    JOB_ERRORS_CAP,
    BackgroundIngestionJob,
    BackgroundIngestionQueue,
)
from .quiet import (  # noqa: E402 — imported after the module constants it depends on
    quiet,  # noqa: E402 — imported after the module constants it depends on
)


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
    # v9.8.0 additive quality fields — advisory only, never gate behavior.
    extraction_quality: Optional[Dict[str, Any]] = None
    warnings: List[str] = field(default_factory=list)
    quality_gate: Optional[Dict[str, Any]] = None

    def as_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
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
        # Additive keys only when populated so pre-v9.8 payloads are unchanged.
        if self.extraction_quality is not None:
            payload["extraction_quality"] = self.extraction_quality
        if self.warnings:
            payload["warnings"] = list(self.warnings)
        if self.quality_gate is not None:
            payload["quality_gate"] = self.quality_gate
        return payload


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
            from .graph.proactive import gate_ingest_candidate
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

    # --- Large candidate #1: background / incremental scheduling (slice) ---
    def schedule_background(
        self,
        items: List[IngestionItem],
        *,
        incremental: bool = True,
        user_email: Optional[str] = None,
    ) -> BackgroundIngestionJob:
        """Schedule items for background incremental indexing.

        Returns a job handle. Actual execution can be driven by caller
        (or future worker) calling pipeline.ingest on each — or through
        :meth:`run_background_job`. This seam enables large-corpus scale
        without blocking user requests.
        """
        job = self._bg_queue.schedule(items, incremental=incremental, user_email=user_email)
        # mark initial status on results concept (jobs track)
        return job

    def get_background_job(self, job_id: str) -> Optional[BackgroundIngestionJob]:
        return self._bg_queue.get(job_id)

    def list_background_jobs(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Recent jobs (newest first) in the frozen ``/api/ingestion`` schema."""
        return [job.as_dict() for job in self._bg_queue.list_recent(limit=limit)]

    def run_background_job(
        self, job_id: str, *, user_email: Optional[str] = None
    ) -> Dict[str, Any]:
        """Execute a queued/interrupted job's remaining items.

        Per-item errors are recorded (capped) and never abort the job. The
        final status is ``completed`` (all done), ``partial`` (some done),
        or ``failed`` (nothing done). Already-completed items are skipped, so
        the same method safely powers both first-run and resume.
        """
        job = self._bg_queue.get(job_id)
        if job is None:
            return {"status": "not_found", "job_id": job_id}
        if job.status == "running":
            return job.as_dict()
        return self._execute_background_job(job, user_email=user_email)

    def resume_background_job(
        self, job_id: str, *, user_email: Optional[str] = None
    ) -> Dict[str, Any]:
        """Resume an interrupted/partial/failed job from its remaining items."""
        return self.run_background_job(job_id, user_email=user_email)

    def _execute_background_job(
        self, job: BackgroundIngestionJob, *, user_email: Optional[str] = None
    ) -> Dict[str, Any]:
        job.status = "running"
        # Retried items get a fresh verdict: reset failure state for this run.
        job.failed = 0
        job.errors = []
        job.touch()
        self._bg_queue.save(job)
        runner_email = user_email or job.user_email
        for index in job.remaining_indices():
            item = job.items[index]
            try:
                result = self.ingest(item, user_email=runner_email or item.owner)
                status, detail = result.status, result.detail
            except Exception as exc:  # noqa: BLE001 — per-item isolation: keep going
                status, detail = "failed", str(exc)
            if status == "ok":
                job.done_indices.add(index)
            else:
                job.record_error(index, item, detail or status)
            job.processed = len(job.done_indices)
            job.touch()
            # Checkpoint per item: a crash here must cost at most the item in
            # flight, never the whole job's progress. One small UPDATE against
            # an ingest (parse + chunk + embed) is noise.
            self._bg_queue.save(job)
        job.processed = len(job.done_indices)
        if job.total == 0 or job.processed >= job.total:
            job.status = "completed"
        elif job.processed > 0:
            job.status = "partial"
        else:
            job.status = "failed"
        job.touch()
        self._bg_queue.save(job)
        return job.as_dict()

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

    # ── multi-modal routing (v11.1.0 Track 3) ────────────────────────────────
    def _modality_for(self, item: IngestionItem, source_type: str) -> str:
        """``image`` / ``audio`` / ``video`` / ``text`` for this item.

        Always ``"text"`` while the flag is off, which is what makes "off" mean
        *unchanged* rather than *slightly different*.
        """
        if not self._allow_multimodal:
            return "text"
        if source_type in IMAGE_SOURCE_TYPES:
            return MODALITY_IMAGE
        if source_type in AUDIO_SOURCE_TYPES:
            return MODALITY_AUDIO
        if source_type in VIDEO_SOURCE_TYPES:
            return MODALITY_VIDEO
        if not item.path:
            return "text"
        return detect_modality(item.path, item.mime_type)

    def _resolve_file_path(self, item: IngestionItem) -> Path:
        if not item.path:
            raise ValueError("File ingestion requires a path.")
        path = Path(item.path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        if path.is_dir():
            raise ValueError(f"File ingestion requires a file, got a directory: {path}")
        return path

    def _ingest_image(self, item, *, source_type, owner, captured_at) -> Dict[str, Any]:
        """Store one picture as an ``Image`` node — OCR, caption, vector.

        The image vector (when a vision model produced one) goes to its own
        index; the OCR/caption text rides the ordinary text index. That split
        is what lets a typed question find a screenshot without ever comparing
        a text vector to an image vector.
        """
        path = self._resolve_file_path(item)
        facts = extract_image_facts(str(path), ports=self._multimodal)
        result = write_image_memory(
            self._kg,
            path=path,
            facts=facts,
            title=item.title or path.name,
            source_type=source_type if source_type in IMAGE_SOURCE_TYPES else MODALITY_IMAGE,
            source_uri=item.source_uri,
            owner=owner,
            workspace_id=item.workspace_id,
            conversation_id=item.conversation_id,
            captured_at=captured_at,
            modified_at=item.modified_at,
            permissions=item.permissions,
            extra_metadata={"mime_type": item.mime_type, **(item.metadata or {})},
        )
        self._record_image_vector(result["node_id"], facts)
        quality = image_quality_score(facts)
        result["extraction_quality"] = {
            "score": quality["score"],
            "level": _quality_level(quality["score"]),
            "reasons": quality["reasons"],
        }
        return result

    def _record_image_vector(self, node_id: str, facts: ImageFacts) -> None:
        """File the image-space vector, if a vision model actually made one."""
        if facts.embedding is None:
            return
        from .graph.image_vectors import record_image_vector

        record_image_vector(
            self._kg,
            node_id=node_id,
            vector=facts.embedding,
            model_id=self._multimodal.vision_model_id or "vision:unnamed",
            space=self._multimodal.vision_space,
            updated_at=utc_now_iso(),
        )

    def _ingest_audio(self, item, *, source_type, owner, captured_at) -> Dict[str, Any]:
        """Store one recording as an ``Audio`` node, transcribed when possible.

        The transcript is text and rides the ordinary text index — chunks,
        concepts, provenance, dedupe all unchanged — but the node itself is a
        recording, because that is what it is whether or not anyone could hear
        it. The recording's own facts stay in the metadata (``modality``,
        ``audio_path``, ``transcription``, ``searchable``). Without a
        transcriber the memory is still kept, and its body says plainly that
        the words were never recognized instead of leaving a blank note.
        """
        path = self._resolve_file_path(item)
        facts = transcribe_audio(str(path), ports=self._multimodal, transcript=item.text)
        title = item.title or path.stem
        body = facts.transcript or (
            f"[{MODALITY_AUDIO}] {title}\n"
            "이 녹음은 아직 글로 바뀌지 않았습니다 — 음성 인식기가 없어 내용 검색은 되지 않습니다."
        )
        result = self._kg.ingest_source(
            source_type=source_type,
            title=title,
            text=body,
            source_uri=item.source_uri or str(path),
            owner=owner,
            workspace_id=item.workspace_id,
            permissions=item.permissions,
            captured_at=captured_at,
            modified_at=item.modified_at,
            conversation_id=item.conversation_id,
            node_type=AUDIO_NODE_TYPE,
            metadata={
                "mime_type": item.mime_type,
                "modality": MODALITY_AUDIO,
                "audio_path": str(path),
                "audio_bytes": path.stat().st_size,
                "transcription": facts.transcription_status,
                "searchable": facts.searchable,
                **({"transcription_detail": facts.detail} if facts.detail else {}),
                **(item.metadata or {}),
            },
        )
        result.setdefault("title", title)
        quality = audio_quality_score(facts)
        result["extraction_quality"] = {
            "score": quality["score"],
            "level": _quality_level(quality["score"]),
            "reasons": quality["reasons"],
        }
        return result

    def _ingest_video(self, item, *, source_type, owner, captured_at) -> Dict[str, Any]:
        """Store one video as keyframes through the image door plus subtitles.

        Nothing here is a new retrieval path: the stills become ordinary
        ``Image`` nodes (OCR, caption, vector, thumbnail) joined by
        ``CONTAINS_IMAGE``, and the subtitle text becomes ordinary chunks. What
        the ``Video`` node adds is the thing they belong to — and an honest
        body when there were no subtitles to read.
        """
        path = self._resolve_file_path(item)
        facts = read_video_facts(
            str(path),
            video_frame_dir(getattr(self._kg, "blob_dir", path.parent), _file_digest(path)),
            count=self._keyframes,
            ports=self._multimodal,
            subtitle_text=item.text,
        )
        result = write_video_memory(
            self._kg,
            path=path,
            facts=facts,
            title=item.title or path.stem,
            source_type=source_type if source_type in VIDEO_SOURCE_TYPES else MODALITY_VIDEO,
            source_uri=item.source_uri,
            owner=owner,
            workspace_id=item.workspace_id,
            conversation_id=item.conversation_id,
            captured_at=captured_at,
            modified_at=item.modified_at,
            permissions=item.permissions,
            extra_metadata={"mime_type": item.mime_type, **(item.metadata or {})},
            ports=self._multimodal,
        )
        quality = video_quality_score(facts)
        result["extraction_quality"] = {
            "score": quality["score"],
            "level": _quality_level(quality["score"]),
            "reasons": quality["reasons"],
        }
        return result

    def _ingest_file(self, item, *, source_type, owner, captured_at) -> Dict[str, Any]:
        path = self._resolve_file_path(item)
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


def _file_digest(path: Path) -> str:
    """Streaming sha256 of a file — the key a video's frame folder is named by."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
