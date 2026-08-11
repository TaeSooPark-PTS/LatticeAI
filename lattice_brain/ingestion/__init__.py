"""Unified ingestion pipeline — the single write-side seam into the Knowledge Graph.

v3.6.0 Knowledge Graph First principle: *no data source bypasses the Knowledge
Graph and no source creates an isolated silo*. Every source — local files,
connected folders, PDFs/Markdown/text/code, web URLs, browser tabs — is
normalized into one :class:`IngestionItem` and pushed through one
:meth:`IngestionPipeline.ingest` entrypoint:

    Source → normalize → content hash → (file | text) ingest → provenance

The pipeline is deliberately thin. It owns normalization, idempotency reporting,
provenance capture, and — crucially — routing every ingest through the shared
``dispatch_tool`` lifecycle so ``pre_tool``/``post_tool`` hooks fire on data
ingestion exactly as they do on tool calls. The heavy graph construction lives in
:class:`knowledge_graph.KnowledgeGraphStore` (``ingest_document`` for files,
``ingest_source`` for text/web), which this module composes rather than
re-implements.

Web ingestion seam
------------------
The graph layer never fetches or parses the web. Fetching, rendering,
readability extraction, and parse quality are the responsibility of the
*upstream* capture surfaces (browser extension, tools layer, MCP servers):
they hand this module already-extracted text. :meth:`IngestionPipeline.
ingest_web_page` is the convenience wrapper for that hand-off — it normalizes
``(url, extracted_text)`` into an ``IngestionItem(source_type="web_url")`` and
routes it through the exact same :meth:`IngestionPipeline.ingest` door as every
other source. If the extracted text is bad, fix the extractor upstream; the
pipeline will not attempt network access or HTML parsing.

Folder ingestion (:meth:`IngestionPipeline.ingest_folder`) walks a local
directory, honors a gitignore-like ``.latticeignore`` file at the root
(blank lines, ``#`` comments, ``fnmatch`` glob patterns, ``dir/`` suffix for
directories), always skips common noise (``.git``, ``node_modules``,
``__pycache__``, virtualenvs, ``dist``, hidden entries by default), applies
size/extension filters, and either ingests inline or schedules through the
existing :class:`BackgroundIngestionQueue`.

Split into cohesive submodules in v11.3.0 (no behaviour change): ``constants``
(routing tables + gates), ``quality`` (advisory scoring), ``models`` (item and
result), ``hashing``, ``folder_scan`` (``.latticeignore``), and the three mixins
``routing`` / ``folders`` / ``jobs_api`` that ``pipeline`` composes into
``IngestionPipeline``. This module re-exports every name the single file
exposed, so ``lattice_brain.ingestion.X`` keeps working.
"""

from __future__ import annotations

# The single file had no ``__all__``, so its public surface was "every module
# global" — including the names it imported for its own use. Every re-export
# below therefore uses the redundant-alias form: it reproduces exactly that
# surface, and it marks each name as deliberate rather than a leftover import.
#
# Stubbing note: rebinding one of these *here* changes only this module's name.
# The submodule that calls it holds its own reference, so a test standing in for
# a helper patches the submodule that uses it.
from ..gates import FeatureGate as FeatureGate
from ..ingestion_jobs import JOB_ERRORS_CAP as JOB_ERRORS_CAP
from ..ingestion_jobs import BackgroundIngestionJob as BackgroundIngestionJob
from ..ingestion_jobs import BackgroundIngestionQueue as BackgroundIngestionQueue
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
from ..multimodal import video_frame_dir as video_frame_dir
from ..multimodal import video_quality_score as video_quality_score
from ..multimodal import write_image_memory as write_image_memory
from ..multimodal import write_video_memory as write_video_memory
from ..quiet import quiet as quiet
from ..runtime.hooks import dispatch_tool as dispatch_tool
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
from .folder_scan import _load_latticeignore as _load_latticeignore
from .folder_scan import _matches_ignore as _matches_ignore
from .folders import IngestionFolderMixin as IngestionFolderMixin
from .hashing import _file_digest as _file_digest
from .hashing import content_hash_text as content_hash_text
from .jobs_api import IngestionJobsMixin as IngestionJobsMixin
from .models import IngestionItem as IngestionItem
from .models import IngestionResult as IngestionResult
from .pipeline import VECTOR_TICK_LIMIT as VECTOR_TICK_LIMIT
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
from .routing import IngestionRoutingMixin as IngestionRoutingMixin
