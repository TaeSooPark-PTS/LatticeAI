"""One door per kind of thing: text, chat, memory record, picture, film, file.

Every method here returns the raw store payload that ``IngestionPipeline.\
ingest`` normalizes; none of them decide *whether* to run. Modality routing
(:meth:`IngestionRoutingMixin._modality_for`) answers ``"text"`` for everything
while multi-modal is off, which is what makes "off" mean *unchanged* rather than
*slightly different*.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from ..multimodal import (
    MODALITY_AUDIO,
    MODALITY_IMAGE,
    MODALITY_VIDEO,
    ImageFacts,
    audio_quality_score,
    detect_modality,
    extract_image_facts,
    image_quality_score,
    read_video_facts,
    transcribe_audio,
    video_frame_dir,
    video_quality_score,
    write_image_memory,
    write_video_memory,
)
from ..utils import utc_now_iso
from ._contract import IngestionCore as _Core
from .constants import (
    _MEMORY_NODE_TYPES,
    AUDIO_NODE_TYPE,
    AUDIO_SOURCE_TYPES,
    IMAGE_SOURCE_TYPES,
    VIDEO_SOURCE_TYPES,
)
from .hashing import _file_digest
from .models import IngestionItem
from .quality import _quality_level


class IngestionRoutingMixin(_Core):
    """The per-source-type ingest doors. Mixed into ``IngestionPipeline``."""

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
        from ..graph.image_vectors import record_image_vector

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
