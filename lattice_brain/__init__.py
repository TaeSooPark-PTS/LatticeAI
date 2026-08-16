"""lattice-brain — the compute half of the Brain Core.

This package hosted the knowledge graph, memory, context assembly,
conversations, the ingest write door, the agent/hook runtime, the workflow
engine, portability and the storage abstraction. v11.6.0 §Wave 2.5 made Rust
the single writer of every one of those, and WP-P1 deleted the Python side.

What remains is what a **pure compute worker** needs, and it is deliberately
small: the always-on hash embedder, the document parser matrix, chunking,
concept/triple extraction, the multi-modal fact readers, and the ingestion
vocabulary both sides hash against. The package still never imports
``latticeai``.

Nothing here is lazy any more, because nothing here is heavy: the modules that
justified the lazy table (the store, the ANN index, the workflow engine) are
gone.
"""

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

__version__ = "11.8.0"

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
