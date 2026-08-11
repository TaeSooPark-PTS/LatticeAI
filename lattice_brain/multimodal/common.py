"""Modality taxonomy, size budgets, and the pure helpers every door shares.

The tables here are the module's own decisions (``.mp4`` is video unless the
capture surface says otherwise), deliberately kept ahead of the platform's
``mimetypes`` answer. Nothing in this file touches a model, a decoder, or the
graph, which is what lets images, audio and video all import it without
importing each other.
"""

from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
from typing import List, Optional

# ── modality taxonomy ────────────────────────────────────────────────────────
MODALITY_TEXT = "text"
MODALITY_IMAGE = "image"
MODALITY_AUDIO = "audio"
MODALITY_VIDEO = "video"

IMAGE_EXTENSIONS = frozenset(
    {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic"}
)
# Containers that are *only* ever audio. ``.mp4``/``.webm`` are deliberately
# absent: by extension alone they are video, and a voice memo recorded in one
# of them arrives through an explicit audio MIME type (or through
# ``VoiceCaptureService``, where the user already said "this is a memo").
# ``.mid``/``.midi`` are listed for a second reason: CPython's *built-in* mime
# table has neither, so ``mimetypes`` answers "audio/midi" only on a host that
# ships a system mime file (macOS reads /etc/apache2/mime.types; a slim Linux
# container has nothing). Leaving them to the fallback let the platform decide
# what a MIDI file is — a module table exists precisely so it does not.
AUDIO_EXTENSIONS = frozenset(
    {".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".opus", ".mid", ".midi"}
)
VIDEO_EXTENSIONS = frozenset({".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v"})
#: Subtitle/caption files a video may arrive with. Same basename, so a
#: ``standup.mp4`` next to a ``standup.srt`` is one memory, not two.
SUBTITLE_EXTENSIONS = ("srt", "vtt")

#: Why a video is recognized and still refused — surfaced to the caller. In
#: 11.1.0 the reason was *scope* (nothing was implemented). Since 11.2.0 the
#: implementation exists and the only remaining reason is a **runtime** one:
#: this machine has no ``ffmpeg``, and inventing frames is not an option.
VIDEO_UNAVAILABLE_DETAIL = (
    "video ingestion needs ffmpeg on this machine and none was found; the file "
    "was not stored (install ffmpeg to enable keyframe extraction)"
)
#: Kept under its 11.1.0 name so existing importers keep working; the reason it
#: carries has changed from "out of scope" to "unavailable on this machine".
VIDEO_OUT_OF_SCOPE = VIDEO_UNAVAILABLE_DETAIL

#: Longest OCR/caption body kept on the node (a screenshot is not a novel).
MAX_INDEX_TEXT_CHARS = 20_000
#: Summary column budget, matching every other ingest door in the graph.
SUMMARY_CHARS = 500
#: Fixed-width chunking for OCR bodies that outgrow the summary.
IMAGE_CHUNK_CHARS = 900
#: Longest edge of the stored thumbnail, in pixels.
THUMBNAIL_EDGE = 96
#: A thumbnail is a UI affordance, not an archive — drop it past this size.
MAX_THUMBNAIL_CHARS = 24_000


def detect_modality(
    path: Optional[str] = None, mime_type: Optional[str] = None
) -> str:
    """``text`` | ``image`` | ``audio`` | ``video`` for one candidate file.

    The declared MIME type wins when it carries a usable top-level type: the
    capture surface saw the bytes, this function only sees a name. Otherwise
    the extension decides, and an unknown extension is ``text`` so existing
    behaviour is untouched.
    """
    declared = str(mime_type or "").strip().lower().split(";")[0].split("/")[0]
    if declared in {MODALITY_IMAGE, MODALITY_AUDIO, MODALITY_VIDEO}:
        return declared
    # The extension tables come before ``mimetypes`` on purpose: they are where
    # this module's decisions live (``.mp4`` is video unless someone who saw
    # the bytes says otherwise), and the stdlib table varies by platform.
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
    """Fixed-width split for OCR bodies — no markdown or code structure here."""
    body = str(text or "").strip()
    if len(body) <= SUMMARY_CHARS:
        return []
    return [
        body[start : start + IMAGE_CHUNK_CHARS]
        for start in range(0, len(body), IMAGE_CHUNK_CHARS)
    ]
