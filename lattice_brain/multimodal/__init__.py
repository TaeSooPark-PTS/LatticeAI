"""Images and audio as first-class memories (v11.1.0, Track 3).

Before this module the Brain could only remember things that arrived as text.
A screenshot of a whiteboard, a photo of a receipt, a voice memo — all of them
either bounced off the ingestion pipeline or landed as an opaque ``Document``
node whose only searchable content was its filename.

What routes here
----------------
:func:`detect_modality` reads the MIME type first and the extension second, and
answers with one of ``text`` / ``image`` / ``audio`` / ``video``. ``video`` is
deliberately a *recognized but unsupported* answer in this release: keyframe
extraction needs a decoder this project does not ship, and returning "video,
out of scope" is worth more than pretending a ``.mov`` is a picture.

What an image memory contains
-----------------------------
:func:`extract_image_facts` gathers only what it can actually observe:

* **dimensions/format** from Pillow (a core dependency);
* **ocr_text** from ``pytesseract`` when it is installed — otherwise
  ``ocr_status="unavailable"`` and no text, never an empty string dressed up as
  a successful read;
* **caption** from an injected vision-language port, and *only* from there. No
  VLM means ``caption is None``. Composing "Image IMG_2381.png (JPEG 3024x4032)"
  and storing it in the caption field would make metadata indistinguishable
  from a model's description forever after;
* **embedding** from an injected vision port, which lives in its own vector
  space (see :mod:`latticeai.core.embedding_providers`) and therefore its own
  index — text queries reach images through OCR/caption text, not by scoring a
  BGE vector against CLIP vectors.

Brain Core owns none of those models. Every heavy dependency arrives as an
injected callable (:class:`MultimodalPorts`), which is also why this module
imports nothing from ``latticeai``.

v11.6.0 removed the *writing* half — ``write_image_memory``,
``write_video_memory``, the keyframe writer and the node-id helpers. Extraction
returns facts; ``lattice-core``'s graph write engine turns them into nodes. What
is left here is exactly what ``POST /worker/multimodal/describe`` and
``POST /worker/asr`` answer with.

Split into cohesive submodules in v11.3.0 (no behaviour change): ``common``
(taxonomy + shared helpers), ``ports`` (injected capabilities + the ffmpeg
fallback), ``images``, ``audio``, ``video``. This module re-exports every name
the single file exposed, so ``lattice_brain.multimodal.X`` keeps working.
"""

from __future__ import annotations

# Internals that predate the split. They are not public API, but they were
# reachable as ``lattice_brain.multimodal.<name>`` before it and callers (and
# tests) still reach them that way, so they are re-exported explicitly. The
# redundant-alias form says "this is a re-export", not a leftover import.
#
# Stubbing note: replacing one of these *here* rebinds only this module's name.
# The submodule that calls it holds its own reference, so a test that wants to
# stand in for ``_which_ffmpeg`` patches ``lattice_brain.multimodal.ports``.
from ..quiet import quiet as quiet
from ..utils import utc_now_iso as utc_now_iso
from .audio import AudioFacts, audio_quality_score, transcribe_audio
from .common import (
    AUDIO_EXTENSIONS,
    IMAGE_CHUNK_CHARS,
    IMAGE_EXTENSIONS,
    MAX_INDEX_TEXT_CHARS,
    MAX_THUMBNAIL_CHARS,
    MODALITY_AUDIO,
    MODALITY_IMAGE,
    MODALITY_TEXT,
    MODALITY_VIDEO,
    SUBTITLE_EXTENSIONS,
    SUMMARY_CHARS,
    THUMBNAIL_EDGE,
    VIDEO_EXTENSIONS,
    VIDEO_OUT_OF_SCOPE,
    VIDEO_UNAVAILABLE_DETAIL,
    detect_modality,
)
from .common import _sha256_file as _sha256_file
from .common import _sha256_text as _sha256_text
from .common import _split_index_text as _split_index_text
from .images import ImageFacts, extract_image_facts, image_quality_score
from .images import _apply_vision_embedding as _apply_vision_embedding
from .images import _open_image as _open_image
from .images import _run_ocr as _run_ocr
from .images import _safe_caption as _safe_caption
from .images import _thumbnail_data_uri as _thumbnail_data_uri
from .ports import (
    DEFAULT_KEYFRAMES,
    FFMPEG_BINARY,
    MultimodalPorts,
    extract_keyframes,
    ffmpeg_available,
)
from .ports import KEYFRAME_TIMEOUT_SECONDS as KEYFRAME_TIMEOUT_SECONDS
from .ports import KEYFRAME_WINDOW as KEYFRAME_WINDOW
from .ports import _injected_keyframes as _injected_keyframes
from .ports import _run_ffmpeg as _run_ffmpeg
from .ports import _which_ffmpeg as _which_ffmpeg
from .video import _CUE_TAG_RE as _CUE_TAG_RE
from .video import _SRT_INDEX_RE as _SRT_INDEX_RE
from .video import _TIMECODE_RE as _TIMECODE_RE
from .video import (
    MAX_SUBTITLE_CHARS,
    VIDEO_FRAME_RELATION,
    VIDEO_FRAME_SOURCE_TYPE,
    VIDEO_NODE_TYPE,
    VideoFacts,
    find_subtitle,
    parse_subtitles,
    read_video_facts,
    video_quality_score,
)

__all__ = [
    "AUDIO_EXTENSIONS",
    "DEFAULT_KEYFRAMES",
    "FFMPEG_BINARY",
    "IMAGE_CHUNK_CHARS",
    "IMAGE_EXTENSIONS",
    "MAX_INDEX_TEXT_CHARS",
    "MAX_SUBTITLE_CHARS",
    "MAX_THUMBNAIL_CHARS",
    "MODALITY_AUDIO",
    "MODALITY_IMAGE",
    "MODALITY_TEXT",
    "MODALITY_VIDEO",
    "SUBTITLE_EXTENSIONS",
    "SUMMARY_CHARS",
    "THUMBNAIL_EDGE",
    "VIDEO_EXTENSIONS",
    "VIDEO_FRAME_RELATION",
    "VIDEO_FRAME_SOURCE_TYPE",
    "VIDEO_NODE_TYPE",
    "VIDEO_OUT_OF_SCOPE",
    "VIDEO_UNAVAILABLE_DETAIL",
    "AudioFacts",
    "ImageFacts",
    "MultimodalPorts",
    "VideoFacts",
    "audio_quality_score",
    "detect_modality",
    "extract_image_facts",
    "extract_keyframes",
    "ffmpeg_available",
    "find_subtitle",
    "image_quality_score",
    "parse_subtitles",
    "read_video_facts",
    "transcribe_audio",
    "video_quality_score",
]
