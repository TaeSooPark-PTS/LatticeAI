"""A video as keyframes through the image door, plus its subtitles as text.

Nothing here is a new retrieval path. The stills go through
:func:`~lattice_brain.multimodal.images.write_image_memory` exactly as a
photograph does, the subtitles become ordinary chunks, and what the ``Video``
node adds is the thing they belong to. Keyframe *extraction* lives in
``ports.py`` beside the decoder it falls back to; this module only consumes it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..quiet import quiet
from ..utils import utc_now_iso
from .common import (
    MAX_INDEX_TEXT_CHARS,
    MODALITY_IMAGE,
    MODALITY_VIDEO,
    SUBTITLE_EXTENSIONS,
    SUMMARY_CHARS,
    _sha256_file,
    _sha256_text,
    _split_index_text,
)
from .images import (
    _attach_concepts,
    _attach_source,
    extract_image_facts,
    write_image_memory,
)
from .ports import DEFAULT_KEYFRAMES, MultimodalPorts, extract_keyframes

#: Graph node type for a video. ``NodeType.VIDEO`` normalizes this on the KG v2
#: write side; the legacy tables keep the label verbatim, like ``Audio``.
VIDEO_NODE_TYPE = "Video"
#: Source type stamped on the extracted stills so a keyframe is never mistaken
#: for a photograph the user took.
VIDEO_FRAME_SOURCE_TYPE = "video_keyframe"
VIDEO_FRAME_RELATION = "CONTAINS_IMAGE"
#: Longest subtitle body kept, matching the OCR/caption ceiling.
MAX_SUBTITLE_CHARS = MAX_INDEX_TEXT_CHARS

_SRT_INDEX_RE = re.compile(r"^\d+$")
_TIMECODE_RE = re.compile(r"\d{1,2}:\d{2}:\d{2}[.,]\d{1,3}\s*-->")
_CUE_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")


def find_subtitle(path: Any) -> Optional[Path]:
    """The ``.srt``/``.vtt`` sitting next to a video under the same basename."""
    video = Path(str(path))
    for suffix in SUBTITLE_EXTENSIONS:
        candidate = video.with_suffix(f".{suffix}")
        if candidate.is_file():
            return candidate
    return None


def parse_subtitles(text: str) -> str:
    """Strip SRT/WebVTT scaffolding down to the words that were said.

    Cue numbers, timecodes, ``WEBVTT`` headers, ``NOTE`` blocks and inline
    ``<c>``/``<i>`` tags carry no recall value and would otherwise dominate a
    chunk. Consecutive duplicate lines (the usual rolling-caption artefact) are
    collapsed. Deliberately a small parser, not a dependency: the format is two
    rules deep and a library here would sit in the ingest path forever.
    """
    lines: List[str] = []
    for raw in str(text or "").splitlines():
        line = raw.strip().lstrip("﻿")
        if not line or line.upper().startswith("WEBVTT") or line.startswith("NOTE"):
            continue
        if _SRT_INDEX_RE.match(line) or _TIMECODE_RE.search(line):
            continue
        cleaned = _CUE_TAG_RE.sub("", line).strip()
        if not cleaned:
            continue
        if lines and lines[-1] == cleaned:
            continue
        lines.append(cleaned)
    return "\n".join(lines)[:MAX_SUBTITLE_CHARS]


@dataclass
class VideoFacts:
    """What could actually be observed in one video, and how each attempt went."""

    path: str
    #: ``ok`` | ``unavailable`` | ``failed`` | ``empty``
    keyframe_status: str = "unavailable"
    keyframes: List[str] = field(default_factory=list)
    keyframe_detail: str = ""
    #: ``ok`` | ``absent`` | ``unreadable`` | ``empty``
    subtitle_status: str = "absent"
    subtitle_path: Optional[str] = None
    subtitle_text: str = ""

    @property
    def searchable(self) -> bool:
        """Whether anything in this video can be matched by a typed question."""
        return bool(self.subtitle_text)

    def as_metadata(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "modality": MODALITY_VIDEO,
            "video_path": self.path,
            "keyframes": len(self.keyframes),
            "keyframe_status": self.keyframe_status,
            "subtitles": self.subtitle_status,
            "searchable": self.searchable,
        }
        if self.keyframe_detail:
            payload["keyframe_detail"] = self.keyframe_detail
        if self.subtitle_path:
            payload["subtitle_path"] = self.subtitle_path
        return payload


def read_video_facts(
    path: Any,
    dest_dir: Any,
    *,
    count: int = DEFAULT_KEYFRAMES,
    ports: Optional[MultimodalPorts] = None,
    subtitle_text: Optional[str] = None,
) -> VideoFacts:
    """Observe one video: its keyframes and its companion subtitles."""
    facts = VideoFacts(path=str(path))
    outcome = extract_keyframes(path, dest_dir, count=count, ports=ports)
    facts.keyframe_status = str(outcome["status"])
    facts.keyframes = list(outcome["frames"])
    facts.keyframe_detail = str(outcome["detail"])
    supplied = str(subtitle_text or "").strip()
    if supplied:
        facts.subtitle_status = "ok"
        facts.subtitle_text = parse_subtitles(supplied)
        return facts
    companion = find_subtitle(path)
    if companion is None:
        return facts
    facts.subtitle_path = str(companion)
    try:
        raw = companion.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        facts.subtitle_status = "unreadable"
        facts.keyframe_detail = (facts.keyframe_detail or "").strip()
        facts.subtitle_text = ""
        quiet()
        facts.subtitle_path = f"{companion} ({exc.strerror or 'unreadable'})"
        return facts
    parsed = parse_subtitles(raw)
    facts.subtitle_status = "ok" if parsed else "empty"
    facts.subtitle_text = parsed
    return facts


def video_quality_score(facts: VideoFacts) -> Dict[str, Any]:
    """How much of this video the Brain can actually retrieve later.

    Same principle as :func:`image_quality_score`: not a judgement about the
    footage. Subtitles are worth the most (they are the words), keyframes are
    worth something (they can be OCR'd and seen), and a video with neither is
    a filename.
    """
    reasons: List[str] = []
    score = 0.1  # we know it is a video and where it lives
    if facts.subtitle_text:
        score += 0.55 * min(1.0, len(facts.subtitle_text) / 400.0)
        reasons.append("subtitles")
    else:
        reasons.append(f"no_subtitles_{facts.subtitle_status}")
    if facts.keyframes:
        score += min(0.35, 0.1 * len(facts.keyframes))
        reasons.append("keyframes")
    else:
        reasons.append(f"no_keyframes_{facts.keyframe_status}")
    return {"score": round(max(0.0, min(1.0, score)), 4), "reasons": reasons}


def video_node_id(content_hash: str, workspace_id: Optional[str] = None) -> str:
    """Workspace-scoped, content-addressed id — re-ingesting is idempotent."""
    scoped = f"{workspace_id or 'legacy-global'}|{content_hash}"
    return f"video:{_sha256_text(scoped)[:24]}"


def video_frame_dir(blob_dir: Any, content_hash: str) -> Path:
    """Where this video's stills live: content-addressed, stable across runs.

    Deliberately under the Brain's own blob directory rather than a temp dir.
    A frame referenced by an ``Image`` node has to still be there the next time
    someone opens that memory, and a backup that copies the blobs copies these.
    """
    return Path(str(blob_dir)) / "video_frames" / str(content_hash)[:32]


def write_video_memory(
    store: Any,
    *,
    path: Path,
    facts: VideoFacts,
    title: str,
    source_type: str = MODALITY_VIDEO,
    source_uri: Optional[str] = None,
    owner: Optional[str] = None,
    workspace_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    captured_at: Optional[str] = None,
    modified_at: Optional[str] = None,
    permissions: Optional[Dict[str, Any]] = None,
    extra_metadata: Optional[Dict[str, Any]] = None,
    ports: Optional[MultimodalPorts] = None,
) -> Dict[str, Any]:
    """Write one ``Video`` node, its keyframes, and its subtitles.

    Each keyframe goes through the **existing image path** — the same
    :func:`extract_image_facts` and :func:`write_image_memory` a photograph
    uses — so a still from a video is OCR'd, captioned, vectorized and made
    searchable by exactly the machinery that already does that, and joined to
    its video by ``CONTAINS_IMAGE``. Subtitles ride the ordinary text index as
    chunks. Nothing about video gets its own retrieval path.

    The frames are written before the video node so their (short-lived) write
    transactions never nest inside the video's.
    """
    ports = ports or MultimodalPorts()
    captured_at = captured_at or utc_now_iso()
    content_hash = _sha256_file(path)
    node_id = video_node_id(content_hash, workspace_id)
    frames = _write_keyframes(
        store,
        facts=facts,
        title=title,
        owner=owner,
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        captured_at=captured_at,
        permissions=permissions,
        ports=ports,
    )
    body = facts.subtitle_text or (
        f"[{MODALITY_VIDEO}] {title}\n"
        "이 영상에는 자막이 없어 말의 내용은 검색되지 않습니다 — "
        "대신 대표 장면 이미지로 찾을 수 있습니다."
    )
    metadata: Dict[str, Any] = {
        "filename": path.name,
        "file_path": str(path),
        "ext": path.suffix.lower(),
        "bytes": path.stat().st_size,
        "sha256": content_hash,
        "content_hash": content_hash,
        "source_type": source_type,
        "source_uri": source_uri or str(path),
        "captured_at": captured_at,
        "modified_at": modified_at,
        "owner": owner,
        "workspace_id": workspace_id,
        "permissions": permissions or {},
        "conversation_id": conversation_id,
        "keyframe_nodes": [frame["node_id"] for frame in frames],
        **facts.as_metadata(),
        **(extra_metadata or {}),
    }
    # Honest card: a video nobody captioned says so, rather than rendering as a
    # blank summary that looks like a failed read.
    summary = body[:SUMMARY_CHARS]
    chunk_ids: List[str] = []
    with store._connect() as conn:
        duplicate = (
            conn.execute("SELECT 1 FROM nodes WHERE id=? LIMIT 1", (node_id,)).fetchone()
            is not None
        )
        store._upsert_node(
            conn,
            node_id,
            VIDEO_NODE_TYPE,
            title or path.name,
            summary=summary,
            metadata=metadata,
            raw=metadata,
            owner=owner,
            workspace_id=workspace_id,
        )
        for frame in frames:
            store._upsert_edge(
                conn,
                node_id,
                frame["node_id"],
                VIDEO_FRAME_RELATION,
                weight=0.8,
                metadata={
                    "source": VIDEO_FRAME_SOURCE_TYPE,
                    "index": frame["index"],
                    "workspace_id": workspace_id,
                },
            )
        for index, piece in enumerate(_split_index_text(facts.subtitle_text)):
            chunk_id = f"chunk:{_sha256_text(f'{node_id}:{index}:{piece}')[:24]}"
            chunk_ids.append(chunk_id)
            chunk_meta = {
                "index": index,
                "source_node": node_id,
                "workspace_id": workspace_id,
                "modality": MODALITY_VIDEO,
            }
            store._upsert_node(
                conn,
                chunk_id,
                "Chunk",
                f"{path.name} chunk {index + 1}",
                summary=piece[:SUMMARY_CHARS],
                metadata=chunk_meta,
                owner=owner,
                workspace_id=workspace_id,
            )
            store._upsert_chunk(
                conn,
                chunk_id=chunk_id,
                source_node=node_id,
                text=piece,
                metadata=chunk_meta,
            )
            store._upsert_edge(conn, node_id, chunk_id, "포함함")
        concept_ids = _attach_concepts(
            store,
            conn,
            node_id=node_id,
            text=facts.subtitle_text,
            owner=owner,
            workspace_id=workspace_id,
        )
        source_node_id = _attach_source(
            store,
            conn,
            node_id=node_id,
            source_type=source_type,
            source_uri=source_uri or str(path),
            title=title or path.name,
            content_hash=content_hash,
            captured_at=captured_at,
            owner=owner,
            workspace_id=workspace_id,
        )
    metadata["concepts"] = concept_ids
    return {
        "node_id": node_id,
        "type": VIDEO_NODE_TYPE,
        "title": title or path.name,
        "sha256": content_hash,
        "content_hash": content_hash,
        "source_node_id": source_node_id,
        "chunk_ids": chunk_ids,
        "chunk_count": len(chunk_ids),
        "duplicate": duplicate,
        "captured_at": captured_at,
        "keyframes": frames,
        "metadata": metadata,
    }


def _write_keyframes(
    store: Any,
    *,
    facts: VideoFacts,
    title: str,
    owner: Optional[str],
    workspace_id: Optional[str],
    conversation_id: Optional[str],
    captured_at: str,
    permissions: Optional[Dict[str, Any]],
    ports: MultimodalPorts,
) -> List[Dict[str, Any]]:
    """Every extracted still, through the ordinary image door."""
    written: List[Dict[str, Any]] = []
    for index, frame_path in enumerate(facts.keyframes):
        frame = Path(frame_path)
        image_facts = extract_image_facts(str(frame), ports=ports)
        if not image_facts.readable:
            # A frame ffmpeg wrote that Pillow cannot open is a state worth
            # skipping, not worth failing the whole video over.
            continue
        result = write_image_memory(
            store,
            path=frame,
            facts=image_facts,
            title=f"{title} · 장면 {index + 1}",
            source_type=VIDEO_FRAME_SOURCE_TYPE,
            source_uri=str(frame),
            owner=owner,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            captured_at=captured_at,
            permissions=permissions,
            extra_metadata={
                "modality": MODALITY_IMAGE,
                "video_path": facts.path,
                "keyframe_index": index,
            },
        )
        written.append({
            "node_id": result["node_id"],
            "index": index,
            "path": str(frame),
            "ocr_status": image_facts.ocr_status,
            "vision_embedding": image_facts.embedding_status,
        })
    return written
