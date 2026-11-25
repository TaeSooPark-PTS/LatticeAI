import os
import platform
import sys
from pathlib import Path

_PLATFORM = platform.system()

AGENT_ROOT = Path(os.getenv("LATTICEAI_AGENT_ROOT") or "agent_workspace").resolve()
MAX_FILE_BYTES = 512_000
MAX_COMMAND_SECONDS = 30
MAX_COMMAND_OUTPUT = 12_000

ALLOWED_GIT_SUBCOMMANDS = {"status", "diff", "log", "show"}

TEXT_EXTENSIONS = {
    ".css",
    ".csv",
    ".html",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".py",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}

DOCUMENT_OUTPUT_DIR = "generated_documents"
PRESENTATION_OUTPUT_DIR = "generated_presentations"
SPREADSHEET_OUTPUT_DIR = "generated_spreadsheets"

PDF_OUTPUT_DIR = "generated_pdfs"
LOCAL_MAX_FILE_BYTES = 2_000_000

_CJK_FONT_CANDIDATES = [
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/Library/Fonts/NanumGothic.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
]

_SUPPORTED_READ_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".xlsx",
    ".pptx",
    ".txt",
    ".md",
    ".csv",
    ".html",
    ".htm",
}
DOCUMENT_MAX_READ_BYTES = 10_000_000


class ToolError(ValueError):
    pass


def _current_agent_root() -> Path:
    tools_mod = sys.modules.get("latticeai.tools")
    if tools_mod is not None and hasattr(tools_mod, "AGENT_ROOT"):
        return Path(tools_mod.AGENT_ROOT)
    return AGENT_ROOT


def ensure_agent_root() -> Path:
    root = _current_agent_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _resolve_path(path: str = "") -> Path:
    root = ensure_agent_root()
    if not path:
        return root
    candidate = (root / path).resolve()
    if candidate != root and root not in candidate.parents:
        raise ToolError("Path escapes the agent workspace.")
    return candidate


def resolve_workspace_path(path: str = "") -> Path:
    return _resolve_path(path)


def _relative(path: Path) -> str:
    return str(path.relative_to(_current_agent_root()))
