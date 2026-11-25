from __future__ import annotations

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
