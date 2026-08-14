"""Ingestion: what a source *is*, how it hashes, and how good it looks.

v3.6.0's Knowledge Graph First principle — *no data source bypasses the
Knowledge Graph and no source creates an isolated silo* — is still the rule.
The door it goes through is no longer here: v11.6.0 §Wave 2.5 made Rust the
single writer of the Brain, so ``IngestionPipeline.ingest``, the per-source
routing doors, the folder walk, the ``.latticeignore`` reader and the durable
background queue moved to ``lattice-core`` / ``lattice-ingest`` /
``lattice-jobs``.

What stays is the vocabulary both sides share, and the compute the write path
asks this worker for:

* ``constants`` — the routing tables and the multi-modal gates;
* ``models`` — :class:`IngestionItem` / :class:`IngestionResult`, now the DTOs
  of a parse request rather than of a write;
* ``hashing`` — ``content_hash_text`` and the file digest, which decide
  idempotency and must produce the same bytes on both sides;
* ``quality`` — the advisory extraction score behind ``POST /worker/parse``;
* ``pipeline`` — the multi-modal capability probe.
"""

from __future__ import annotations

# Re-exports use the redundant-alias form: it says "this is a re-export", not a
# leftover import.
#
# Stubbing note: rebinding one of these *here* changes only this module's name.
# The submodule that calls it holds its own reference, so a test standing in for
# a helper patches the submodule that uses it.
from ..gates import FeatureGate as FeatureGate
from ..multimodal import AUDIO_EXTENSIONS as AUDIO_EXTENSIONS
from ..multimodal import DEFAULT_KEYFRAMES as DEFAULT_KEYFRAMES
from ..multimodal import IMAGE_EXTENSIONS as IMAGE_EXTENSIONS
from ..multimodal import MODALITY_AUDIO as MODALITY_AUDIO
from ..multimodal import MODALITY_IMAGE as MODALITY_IMAGE
from ..multimodal import MODALITY_VIDEO as MODALITY_VIDEO
from ..multimodal import VIDEO_EXTENSIONS as VIDEO_EXTENSIONS
from ..multimodal import VIDEO_UNAVAILABLE_DETAIL as VIDEO_UNAVAILABLE_DETAIL
from ..multimodal import ImageFacts as ImageFacts
from ..multimodal import MultimodalPorts as MultimodalPorts
from ..multimodal import audio_quality_score as audio_quality_score
from ..multimodal import detect_modality as detect_modality
from ..multimodal import extract_image_facts as extract_image_facts
from ..multimodal import ffmpeg_available as ffmpeg_available
from ..multimodal import image_quality_score as image_quality_score
from ..multimodal import read_video_facts as read_video_facts
from ..multimodal import transcribe_audio as transcribe_audio
from ..multimodal import video_quality_score as video_quality_score
from ..quiet import quiet as quiet
from ..utils import utc_now_iso as utc_now_iso
from .constants import _MEMORY_NODE_TYPES as _MEMORY_NODE_TYPES
from .constants import ALLOW_MULTIMODAL_ENV as ALLOW_MULTIMODAL_ENV
from .constants import ALLOW_VIDEO_ENV as ALLOW_VIDEO_ENV
from .constants import AUDIO_NODE_TYPE as AUDIO_NODE_TYPE
from .constants import AUDIO_SOURCE_TYPES as AUDIO_SOURCE_TYPES
from .constants import AUTO_VECTOR_INDEX_ENV as AUTO_VECTOR_INDEX_ENV
from .constants import AUTO_VECTOR_INDEX_GATE as AUTO_VECTOR_INDEX_GATE
from .constants import CHAT_SOURCE_TYPES as CHAT_SOURCE_TYPES
from .constants import DEFAULT_FOLDER_EXTENSIONS as DEFAULT_FOLDER_EXTENSIONS
from .constants import DEFAULT_MAX_FILE_BYTES as DEFAULT_MAX_FILE_BYTES
from .constants import DEFAULT_MAX_TEXT_BYTES as DEFAULT_MAX_TEXT_BYTES
from .constants import FILE_SOURCE_TYPES as FILE_SOURCE_TYPES
from .constants import FOLDER_CODE_EXTENSIONS as FOLDER_CODE_EXTENSIONS
from .constants import FOLDER_DEFAULT_SKIP_DIRS as FOLDER_DEFAULT_SKIP_DIRS
from .constants import FOLDER_DOCUMENT_EXTENSIONS as FOLDER_DOCUMENT_EXTENSIONS
from .constants import FOLDER_MULTIMODAL_EXTENSIONS as FOLDER_MULTIMODAL_EXTENSIONS
from .constants import FOLDER_TEXT_EXTENSIONS as FOLDER_TEXT_EXTENSIONS
from .constants import FOLDER_VIDEO_EXTENSIONS as FOLDER_VIDEO_EXTENSIONS
from .constants import IMAGE_SOURCE_TYPES as IMAGE_SOURCE_TYPES
from .constants import LATTICEIGNORE_FILENAME as LATTICEIGNORE_FILENAME
from .constants import MEMORY_SOURCE_TYPES as MEMORY_SOURCE_TYPES
from .constants import MULTIMODAL_GATE as MULTIMODAL_GATE
from .constants import TEXT_SOURCE_TYPES as TEXT_SOURCE_TYPES
from .constants import VIDEO_GATE as VIDEO_GATE
from .constants import VIDEO_SOURCE_TYPES as VIDEO_SOURCE_TYPES
from .hashing import _file_digest as _file_digest
from .hashing import content_hash_text as content_hash_text
from .models import IngestionItem as IngestionItem
from .models import IngestionResult as IngestionResult
from .pipeline import IngestionPipeline as IngestionPipeline
from .quality import _BOILERPLATE_LINE_MARKERS as _BOILERPLATE_LINE_MARKERS
from .quality import _CAPTURE_REASON_LABELS as _CAPTURE_REASON_LABELS
from .quality import _WEB_SOURCE_TYPES as _WEB_SOURCE_TYPES
from .quality import CAPTURE_SUGGESTIONS_THIN as CAPTURE_SUGGESTIONS_THIN
from .quality import QUALITY_HIGH_THRESHOLD as QUALITY_HIGH_THRESHOLD
from .quality import QUALITY_LOW_THRESHOLD as QUALITY_LOW_THRESHOLD
from .quality import QUALITY_LOW_WARNING as QUALITY_LOW_WARNING
from .quality import _quality_level as _quality_level
from .quality import assess_extraction_quality as assess_extraction_quality
from .quality import capture_quality_verdict as capture_quality_verdict
