from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
from typing import List, Optional

MODALITY_TEXT = "text"
MODALITY_IMAGE = "image"
MODALITY_AUDIO = "audio"
MODALITY_VIDEO = "video"

IMAGE_EXTENSIONS = frozenset(
    {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic"}
)
AUDIO_EXTENSIONS = frozenset(
    {".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".opus", ".mid", ".midi"}
)
VIDEO_EXTENSIONS = frozenset({".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v"})
SUBTITLE_EXTENSIONS = ("srt", "vtt")

VIDEO_UNAVAILABLE_DETAIL = (
    "video ingestion needs ffmpeg on this machine and none was found; the file "
    "was not stored (install ffmpeg to enable keyframe extraction)"
)
VIDEO_OUT_OF_SCOPE = VIDEO_UNAVAILABLE_DETAIL

MAX_INDEX_TEXT_CHARS = 20_000
SUMMARY_CHARS = 500
IMAGE_CHUNK_CHARS = 900
THUMBNAIL_EDGE = 96
MAX_THUMBNAIL_CHARS = 24_000


def detect_modality(path: Optional[str] = None, mime_type: Optional[str] = None) -> str:
    declared = str(mime_type or "").strip().lower().split(";")[0].split("/")[0]
    if declared in {MODALITY_IMAGE, MODALITY_AUDIO, MODALITY_VIDEO}:
        return declared
    suffix = Path(str(path or "")).suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return MODALITY_IMAGE
    if suffix in AUDIO_EXTENSIONS:
        return MODALITY_AUDIO
    if suffix in VIDEO_EXTENSIONS:
        return MODALITY_VIDEO
    if path:
        guessed, _ = mimetypes.guess_type(str(path))
        top = str(guessed or "").split("/")[0]
        if top in {MODALITY_IMAGE, MODALITY_AUDIO, MODALITY_VIDEO}:
            return top
    return MODALITY_TEXT


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8", "ignore")).hexdigest()


def _split_index_text(text: str) -> List[str]:
    body = str(text or "").strip()
    if len(body) <= SUMMARY_CHARS:
        return []
    return [
        body[start : start + IMAGE_CHUNK_CHARS]
        for start in range(0, len(body), IMAGE_CHUNK_CHARS)
    ]
