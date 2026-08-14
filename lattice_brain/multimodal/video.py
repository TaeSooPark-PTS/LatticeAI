"""What can be read off a video file: its container facts and its subtitles.

Writing the ``Video`` node, the keyframe stills and the edges between them is
``lattice-core``'s since v11.6.0. What is left is the reading:
:func:`read_video_facts` opens the container, :func:`find_subtitle` looks for
the ``.srt``/``.vtt`` beside it and :func:`parse_subtitles` turns cues into
plain text. Keyframe *extraction* lives in ``ports.py`` beside the decoder it
falls back to.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..quiet import quiet
from .common import (
    MAX_INDEX_TEXT_CHARS,
    MODALITY_VIDEO,
    SUBTITLE_EXTENSIONS,
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


