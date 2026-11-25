from __future__ import annotations

from ..gates import FeatureGate
from ..multimodal import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS, VIDEO_EXTENSIONS

FILE_SOURCE_TYPES = frozenset({"file", "local_file", "upload", "pdf"})
TEXT_SOURCE_TYPES = frozenset(
    {"web_url", "browser_tab", "text", "markdown", "note", "code", "clipboard"}
)
CHAT_SOURCE_TYPES = frozenset({"chat_message", "mcp_message"})
MEMORY_SOURCE_TYPES = frozenset({"decision", "experience", "workspace_event"})
_MEMORY_NODE_TYPES = {
    "decision": "Decision",
    "experience": "Experience",
    "workspace_event": "Event",
}

DEFAULT_MAX_TEXT_BYTES = 5 * 1024 * 1024


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
FOLDER_TEXT_EXTENSIONS = frozenset(
    {
        ".txt",
        ".md",
        ".markdown",
        ".rst",
        ".csv",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
    }
)
FOLDER_CODE_EXTENSIONS = frozenset(
    {
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".html",
        ".css",
        ".go",
        ".rs",
        ".java",
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".rb",
        ".php",
        ".swift",
        ".kt",
        ".sh",
        ".sql",
    }
)
FOLDER_DOCUMENT_EXTENSIONS = frozenset({".pdf"})
DEFAULT_FOLDER_EXTENSIONS = (
    FOLDER_TEXT_EXTENSIONS | FOLDER_CODE_EXTENSIONS | FOLDER_DOCUMENT_EXTENSIONS
)
DEFAULT_MAX_FILE_BYTES = 4_000_000
LATTICEIGNORE_FILENAME = ".latticeignore"
AUTO_VECTOR_INDEX_ENV = "LATTICEAI_AUTO_VECTOR_INDEX"
AUTO_VECTOR_INDEX_GATE = FeatureGate(
    AUTO_VECTOR_INDEX_ENV,
    default=True,
    name="auto_vector_index",
    detail="New material is prepared for semantic search as soon as it lands.",
)

ALLOW_MULTIMODAL_ENV = "LATTICEAI_ALLOW_MULTIMODAL"
MULTIMODAL_GATE = FeatureGate(
    ALLOW_MULTIMODAL_ENV,
    default=False,
    name="allow_multimodal",
    detail="Pictures and recordings are only ingested when this is turned on.",
)
ALLOW_VIDEO_ENV = "LATTICEAI_ALLOW_VIDEO"
VIDEO_GATE = FeatureGate(
    ALLOW_VIDEO_ENV,
    default=True,
    name="allow_video",
    detail="Videos are ingested as keyframes plus subtitles when multi-modal is on.",
)
IMAGE_SOURCE_TYPES = frozenset({"image", "screenshot", "photo"})
AUDIO_SOURCE_TYPES = frozenset({"audio", "voice_memo", "recording"})
VIDEO_SOURCE_TYPES = frozenset({"video", "screen_recording", "movie"})
FOLDER_MULTIMODAL_EXTENSIONS = IMAGE_EXTENSIONS | AUDIO_EXTENSIONS
FOLDER_VIDEO_EXTENSIONS = VIDEO_EXTENSIONS
AUDIO_NODE_TYPE = "Audio"
