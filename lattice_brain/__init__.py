from .embeddings import LocalEmbeddingModel
from .multimodal import (
    AudioFacts,
    ImageFacts,
    MultimodalPorts,
    VideoFacts,
    detect_modality,
    extract_image_facts,
    extract_keyframes,
    ffmpeg_available,
    parse_subtitles,
    read_video_facts,
    transcribe_audio,
)

__version__ = "14.6.0"

__all__ = [
    "AudioFacts",
    "ImageFacts",
    "LocalEmbeddingModel",
    "MultimodalPorts",
    "VideoFacts",
    "detect_modality",
    "extract_image_facts",
    "extract_keyframes",
    "ffmpeg_available",
    "parse_subtitles",
    "read_video_facts",
    "transcribe_audio",
    "__version__",
]
