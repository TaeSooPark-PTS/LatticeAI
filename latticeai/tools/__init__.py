"""Safe local tools for Lattice AI agent mode.

All filesystem operations are confined to LATTICEAI_AGENT_ROOT, defaulting to
./agent_workspace. Command execution runs without a shell and from inside that
workspace.

The physical implementation belongs to ``latticeai.tools``. The historical
root ``tools`` package aliases this exact module object, preserving existing
imports and ``tools.AGENT_ROOT`` monkeypatch/state identity.
"""

import base64
import json
import os
import platform
import re
import shlex
import socket
import subprocess
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from latticeai.core.tool_registry import ToolRegistry
from p_reinforce import BRAIN_DIR, STRUCTURE

_PLATFORM = platform.system()  # "Darwin" | "Windows" | "Linux"


# ── base: agent-root sandbox, shared constants, path helpers ──────────────────
AGENT_ROOT = Path(os.getenv("LATTICEAI_AGENT_ROOT") or "agent_workspace").resolve()
MAX_FILE_BYTES = 512_000
MAX_COMMAND_SECONDS = 30
MAX_BUILD_SECONDS = 180
MAX_DEPLOY_SECONDS = 300
MAX_COMMAND_OUTPUT = 12_000

BLOCKED_COMMANDS = {
    "rm",
    "rmdir",
    "sudo",
    "su",
    "chmod",
    "chown",
    "curl",
    "wget",
    "ssh",
    "scp",
    "rsync",
    "dd",
    "mkfs",
    "diskutil",
    "launchctl",
}

ALLOWED_COMMANDS = {
    "pwd",
    "ls",
    "find",
    "cat",
    "head",
    "tail",
    "wc",
    "rg",
    "git",
}

BUILD_SCRIPT_NAMES = {"build", "compile", "typecheck", "test"}
DEPLOY_SCRIPT_NAMES = {
    "deploy",
    "preview",
    "release",
    "package",
    "dist",
    "make",
    "build:installer",
    "build:pkg",
    "build:exe",
    "package:mac",
    "package:win",
}

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


class ToolError(ValueError):
    pass


def ensure_agent_root() -> Path:
    AGENT_ROOT.mkdir(parents=True, exist_ok=True)
    return AGENT_ROOT


def _resolve_path(path: str = "") -> Path:
    ensure_agent_root()
    if not path:
        return AGENT_ROOT
    candidate = (AGENT_ROOT / path).resolve()
    if candidate != AGENT_ROOT and AGENT_ROOT not in candidate.parents:
        raise ToolError("Path escapes the agent workspace.")
    return candidate


def resolve_workspace_path(path: str = "") -> Path:
    """Public alias of the sandboxed workspace path resolver (v9.6.0)."""
    return _resolve_path(path)


def _relative(path: Path) -> str:
    return str(path.relative_to(AGENT_ROOT))


# ── document / local / read constants (shared by submodules) ──────────────────
PDF_OUTPUT_DIR = "generated_pdfs"
LOCAL_MAX_FILE_BYTES = 2_000_000  # 2 MB cap for local reads


# CJK-capable fonts (Korean + Chinese + Japanese)
_CJK_FONT_CANDIDATES = [
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",   # Korean (macOS)
    "/System/Library/Fonts/STHeiti Light.ttc",       # Chinese (macOS)
    "/System/Library/Fonts/PingFang.ttc",            # Chinese (macOS)
    "/Library/Fonts/NanumGothic.ttf",               # Korean
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
]

_SUPPORTED_READ_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".pptx", ".txt", ".md", ".csv"}
DOCUMENT_MAX_READ_BYTES = 10_000_000  # 10 MB


# ── focused tool submodules (re-exported flat for import compatibility) ───────
from latticeai.tools.computer import *  # noqa: E402,F401,F403
from latticeai.tools.filesystem import *  # noqa: E402,F401,F403
from latticeai.tools.documents import *  # noqa: E402,F401,F403
from latticeai.tools.local_files import *  # noqa: E402,F401,F403
from latticeai.tools.knowledge import *  # noqa: E402,F401,F403
from latticeai.tools.network import *  # noqa: E402,F401,F403
from latticeai.tools.commands import *  # noqa: E402,F401,F403


# ── tool registry: the single name → invocation source of truth ───────────────
def _h_create_xlsx(args: Dict[str, Any]) -> Dict[str, Any]:
    rows = args.get("rows", [])
    if isinstance(rows, str):
        rows = json.loads(rows)
    return create_xlsx(rows, args.get("filename", "spreadsheet.xlsx"), args.get("sheet_name", "Sheet1"))


def _h_create_pptx(args: Dict[str, Any]) -> Dict[str, Any]:
    slides = args.get("slides", [])
    if isinstance(slides, str):
        slides = json.loads(slides)
    return create_pptx(args.get("title", ""), slides, args.get("filename", "presentation.pptx"))


def _knowledge_scope(args: Dict[str, Any]) -> Dict[str, str]:
    """Return the authenticated scope injected by server/runtime adapters."""
    workspace_id = str(args.get("workspace_id") or "").strip()
    user_email = str(args.get("user_email") or "").strip().lower()
    if not workspace_id or not user_email:
        raise ToolError(
            "Knowledge tool execution requires an authenticated workspace and user scope."
        )
    return {"workspace_id": workspace_id, "user_email": user_email}


# ── Tool registry: the single source of truth for name → invocation ───────────
# Each entry binds the args dict to a tool function. ``execute_tool`` is a
# lookup over this table — adding a tool means adding one entry here, not
# editing an if/elif chain. server.py's governance map and catalog brief are
# checked against ``registered_tools()`` so the three never silently drift.
TOOL_HANDLERS: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
    # filesystem
    "list_dir":          lambda a: list_dir(a.get("path", ".")),
    "workspace_tree":    lambda a: workspace_tree(a.get("path", "."), a.get("max_depth", 3)),
    "read_file":         lambda a: read_file(a["path"], offset=a.get("offset", 0), limit=a.get("limit", 0), line_numbers=a.get("line_numbers", True)),
    "write_file":        lambda a: write_file(a["path"], a.get("content", "")),
    "edit_file":         lambda a: edit_file(a["path"], a["old_string"], a["new_string"], replace_all=bool(a.get("replace_all", False))),
    "grep":              lambda a: grep(a["pattern"], path=a.get("path", "."), glob=a.get("glob"), max_results=a.get("max_results", 50), case_insensitive=bool(a.get("case_insensitive", False)), context_lines=a.get("context_lines", 0)),
    "search_files":      lambda a: search_files(a["query"], a.get("path", "."), a.get("max_results", 20)),
    "inspect_html":      lambda a: inspect_html(a["path"]),
    "preview_url":       lambda a: preview_url(a.get("path", "index.html")),
    # planning
    "todo_read":         lambda a: todo_read(),
    "todo_write":        lambda a: todo_write(a.get("todos") or []),
    # documents
    "create_docx":       lambda a: create_docx(a.get("title", ""), a.get("body", ""), a.get("filename", "document.docx")),
    "create_xlsx":       _h_create_xlsx,
    "create_pptx":       _h_create_pptx,
    "create_pdf":        lambda a: create_pdf(a.get("title", ""), a.get("body", ""), a.get("filename", "document.pdf")),
    "create_web_project": lambda a: create_web_project(a.get("path", ""), a.get("framework", "react"), a.get("template", "vite")),
    # local filesystem
    "local_list":        lambda a: local_list(a["path"]),
    "local_read":        lambda a: local_read(a["path"]),
    "local_write":       lambda a: local_write(a["path"], a.get("content", "")),
    "read_document":     lambda a: read_document(a["path"]),
    "network_status":    lambda a: network_status(),
    # computer use
    "computer_screenshot": lambda a: computer_screenshot(),
    "computer_open_app": lambda a: computer_open_app(a.get("app", "Google Chrome")),
    "computer_open_url": lambda a: computer_open_url(a["url"], a.get("app", "Google Chrome")),
    "computer_click":    lambda a: computer_click(a.get("x", 0), a.get("y", 0), a.get("button", "left"), a.get("double", False)),
    "computer_type":     lambda a: computer_type(a["text"], a.get("interval", 0.04)),
    "computer_key":      lambda a: computer_key(a["key"]),
    "computer_scroll":   lambda a: computer_scroll(a.get("x", 0), a.get("y", 0), a.get("direction", "down"), a.get("clicks", 3)),
    "computer_move":     lambda a: computer_move(a.get("x", 0), a.get("y", 0)),
    "computer_drag":     lambda a: computer_drag(a.get("x1", 0), a.get("y1", 0), a.get("x2", 0), a.get("y2", 0)),
    "computer_status":   lambda a: computer_status(),
    "chrome_status":     lambda a: desktop_bridge_status(),
    "computer_use_status": lambda a: desktop_bridge_status(),
    "vision_analyze":    lambda a: vision_analyze(a.get("image_b64", ""), a.get("prompt", "Describe this image in detail. Be concise.")),
    # knowledge / obsidian
    "knowledge_save":    lambda a: knowledge_save(a["content"], a.get("folder", "00_Raw"), a.get("title"), **_knowledge_scope(a)),
    "knowledge_search":  lambda a: knowledge_search(a["query"], a.get("max_results", 5), **_knowledge_scope(a)),
    "knowledge_tree":    lambda a: knowledge_tree(**_knowledge_scope(a)),
    "obsidian_save":     lambda a: obsidian_save(a["content"], a.get("folder", "00_Raw"), a.get("title"), **_knowledge_scope(a)),
    "obsidian_search":   lambda a: obsidian_search(a["query"], a.get("max_results", 5), **_knowledge_scope(a)),
    "obsidian_tree":     lambda a: obsidian_tree(**_knowledge_scope(a)),
    # git (read-only)
    "git_status":        lambda a: git_status(a.get("cwd")),
    "git_diff":          lambda a: git_diff(a.get("path"), a.get("cwd")),
    "git_log":           lambda a: git_log(a.get("max_count", 5), a.get("cwd")),
    "git_show":          lambda a: git_show(a.get("revision", "HEAD"), a.get("cwd")),
    # exec
    "run_command":       lambda a: run_command(a["command"], a.get("cwd")),
    "build_project":     lambda a: build_project(a.get("cwd"), a.get("script", "build")),
    "deploy_project":    lambda a: deploy_project(a.get("cwd"), a.get("script", "deploy")),
}


DEFAULT_TOOL_REGISTRY = ToolRegistry(TOOL_HANDLERS)


def registered_tools() -> frozenset:
    """Names dispatchable through ``execute_tool`` — the seam other modules verify against."""
    return DEFAULT_TOOL_REGISTRY.registered_tools()


def execute_tool(action: str, args: Dict[str, Any]) -> Dict[str, Any]:
    return DEFAULT_TOOL_REGISTRY.execute(action, args, error_cls=ToolError)


__all__ = [
    "AGENT_ROOT", "ToolError", "ensure_agent_root", "resolve_workspace_path",
    "list_dir", "workspace_tree", "read_file", "write_file", "edit_file", "grep",
    "search_files", "inspect_html", "preview_url", "create_web_project",
    "todo_read", "todo_write",
    "create_docx", "create_xlsx", "create_pptx", "create_pdf", "read_document",
    "local_list", "local_read", "local_write", "desktop_bridge_status",
    "knowledge_save", "knowledge_search", "knowledge_tree", "knowledge_scope_root",
    "obsidian_save", "obsidian_search", "obsidian_tree",
    "network_status",
    "computer_screenshot", "computer_open_app", "computer_open_url",
    "computer_click", "computer_type", "computer_key", "computer_scroll",
    "computer_move", "computer_drag", "computer_status", "vision_analyze",
    "run_command", "build_project", "deploy_project",
    "git_status", "git_diff", "git_log", "git_show",
    "TOOL_HANDLERS", "DEFAULT_TOOL_REGISTRY", "registered_tools", "execute_tool",
    "BRAIN_DIR", "STRUCTURE",
]
