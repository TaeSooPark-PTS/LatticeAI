"""What routes where, what a folder scan admits, and which gates decide.

Source-type sets, folder-scan filters, size budgets and the four feature gates,
with no logic beyond them. Every other submodule imports this one; this one
imports nothing from the package, which is what keeps the layering acyclic.
"""

from __future__ import annotations

from ..gates import FeatureGate
from ..multimodal import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS, VIDEO_EXTENSIONS

# Source types that arrive as a file on disk (read via ingest_document).
FILE_SOURCE_TYPES = frozenset({"file", "local_file", "upload", "pdf"})
# Source types that arrive as extracted text (read via ingest_source).
TEXT_SOURCE_TYPES = frozenset(
    {"web_url", "browser_tab", "text", "markdown", "note", "code", "clipboard"}
)
# Conversational exchanges (read via ingest_message — role/content semantics,
# conversation chaining). v4: chat and MCP messages stop bypassing the
# pipeline, so they carry provenance and fire the hook lifecycle like every
# other source.
CHAT_SOURCE_TYPES = frozenset({"chat_message", "mcp_message"})
# Typed memory records (read via ingest_event → Decision/Experience/Event
# nodes). The Memory System writes through the same door as everything else.
MEMORY_SOURCE_TYPES = frozenset({"decision", "experience", "workspace_event"})
_MEMORY_NODE_TYPES = {"decision": "Decision", "experience": "Experience", "workspace_event": "Event"}

DEFAULT_MAX_TEXT_BYTES = 5 * 1024 * 1024  # 5 MB of extracted text per item


# ── Folder ingestion (ingest_folder) filters ─────────────────────────────────
# Directories that are always pruned regardless of .latticeignore.
FOLDER_DEFAULT_SKIP_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "env",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".next",
        "target",
        ".cache",
        ".idea",
        ".vscode",
    }
)
# Extension filter matching FILE_SOURCE_TYPES conventions: text/markdown/code
# are read inline (extracted content → chunks); .pdf routes as source_type
# "pdf" through ingest_document (content extraction is upstream's concern).
FOLDER_TEXT_EXTENSIONS = frozenset(
    {".txt", ".md", ".markdown", ".rst", ".csv", ".json", ".yaml", ".yml", ".toml", ".ini"}
)
FOLDER_CODE_EXTENSIONS = frozenset(
    {
        ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".go", ".rs",
        ".java", ".c", ".h", ".cpp", ".hpp", ".rb", ".php", ".swift", ".kt",
        ".sh", ".sql",
    }
)
FOLDER_DOCUMENT_EXTENSIONS = frozenset({".pdf"})
DEFAULT_FOLDER_EXTENSIONS = (
    FOLDER_TEXT_EXTENSIONS | FOLDER_CODE_EXTENSIONS | FOLDER_DOCUMENT_EXTENSIONS
)
DEFAULT_MAX_FILE_BYTES = 4_000_000  # matches the local-index text/code budget
LATTICEIGNORE_FILENAME = ".latticeignore"
# Opt-out escape hatch for the post-ingest incremental vector sync.
AUTO_VECTOR_INDEX_ENV = "LATTICEAI_AUTO_VECTOR_INDEX"
#: Default *on*, unlike every other gate here: new material has always been made
#: searchable straight away, and this exists so a settings surface can turn that
#: off (batch reindex later) without a restart. ``FeatureGate`` parses the env
#: var with the same words the hand-written opt-out check used, so an untouched
#: install — including one with a nonsense value — answers exactly as before.
AUTO_VECTOR_INDEX_GATE = FeatureGate(
    AUTO_VECTOR_INDEX_ENV,
    default=True,
    name="auto_vector_index",
    detail="New material is prepared for semantic search as soon as it lands.",
)

# ── Multi-modal ingestion (v11.1.0 Track 3) ──────────────────────────────────
# Opt-in, default off, on purpose. Turning it on changes what a folder scan
# *stores* (pictures and recordings, with OCR and — if a model is loaded —
# captions and vectors), and that is the user's call, not a default. With the
# flag off every routing decision below is skipped and behaviour is byte-for-
# byte what it was before this release.
ALLOW_MULTIMODAL_ENV = "LATTICEAI_ALLOW_MULTIMODAL"
#: The multi-modal switch, resolved when it is asked rather than frozen into
#: ``self`` at construction (v11.2.0). The environment variable is still the
#: answer for an untouched install — same var, same words, same default off —
#: but a settings surface can bind a resolver and move it without a restart.
MULTIMODAL_GATE = FeatureGate(
    ALLOW_MULTIMODAL_ENV,
    default=False,
    name="allow_multimodal",
    detail="Pictures and recordings are only ingested when this is turned on.",
)
#: Video is a *sub-switch* of the one above: with multi-modal off nothing about
#: video happens at all, and with it on video is included unless this is
#: explicitly turned off. The effective default is therefore still "no video",
#: and the seam exists so a settings screen can offer pictures without films.
ALLOW_VIDEO_ENV = "LATTICEAI_ALLOW_VIDEO"
VIDEO_GATE = FeatureGate(
    ALLOW_VIDEO_ENV,
    default=True,
    name="allow_video",
    detail="Videos are ingested as keyframes plus subtitles when multi-modal is on.",
)
#: Source types that name a modality outright (a caller who already knows).
IMAGE_SOURCE_TYPES = frozenset({"image", "screenshot", "photo"})
AUDIO_SOURCE_TYPES = frozenset({"audio", "voice_memo", "recording"})
VIDEO_SOURCE_TYPES = frozenset({"video", "screen_recording", "movie"})
#: Added to the folder-scan allow-list only while multimodal is enabled.
FOLDER_MULTIMODAL_EXTENSIONS = IMAGE_EXTENSIONS | AUDIO_EXTENSIONS
#: Videos join the folder allow-list only when this machine can decode one —
#: scanning a folder into a pile of refusals is not a feature.
FOLDER_VIDEO_EXTENSIONS = VIDEO_EXTENSIONS
#: Graph node type for a recording. ``NodeType.AUDIO`` normalizes this on the
#: KG v2 write side; the legacy tables keep the label verbatim, which is what
#: every type-aware read (graph view, context sections, doc-gen) matches on.
AUDIO_NODE_TYPE = "Audio"
