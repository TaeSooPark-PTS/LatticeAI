from typing import Any, Callable, Dict

from latticeai.core.tool_registry import ToolRegistry
from latticeai.services.p_reinforce import BRAIN_DIR, STRUCTURE
from latticeai.tools._shared import (
    _CJK_FONT_CANDIDATES,
    _PLATFORM,
    _SUPPORTED_READ_EXTENSIONS,
    AGENT_ROOT,
    ALLOWED_GIT_SUBCOMMANDS,
    DOCUMENT_MAX_READ_BYTES,
    DOCUMENT_OUTPUT_DIR,
    LOCAL_MAX_FILE_BYTES,
    MAX_COMMAND_OUTPUT,
    MAX_COMMAND_SECONDS,
    MAX_FILE_BYTES,
    PDF_OUTPUT_DIR,
    PRESENTATION_OUTPUT_DIR,
    SPREADSHEET_OUTPUT_DIR,
    TEXT_EXTENSIONS,
    ToolError,
    _relative,
    _resolve_path,
    ensure_agent_root,
    resolve_workspace_path,
)
from latticeai.tools.commands import *
from latticeai.tools.computer import *
from latticeai.tools.documents import *
from latticeai.tools.filesystem import *
from latticeai.tools.knowledge import *
from latticeai.tools.local_files import *
from latticeai.tools.network import *
from latticeai.tools.web import *


def _knowledge_scope(args: Dict[str, Any]) -> Dict[str, str]:
    workspace_id = str(args.get("workspace_id") or "").strip()
    user_email = str(args.get("user_email") or "").strip().lower()
    if not workspace_id or not user_email:
        raise ToolError(
            "Knowledge tool execution requires an authenticated workspace and user scope."
        )
    return {"workspace_id": workspace_id, "user_email": user_email}


TOOL_HANDLERS: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
    "list_dir": lambda a: list_dir(a.get("path", ".")),
    "workspace_tree": lambda a: workspace_tree(
        a.get("path", "."), a.get("max_depth", 3)
    ),
    "read_file": lambda a: read_file(
        a["path"],
        offset=a.get("offset", 0),
        limit=a.get("limit", 0),
        line_numbers=a.get("line_numbers", True),
    ),
    "grep": lambda a: grep(
        a["pattern"],
        path=a.get("path", "."),
        glob=a.get("glob"),
        max_results=a.get("max_results", 50),
        case_insensitive=bool(a.get("case_insensitive", False)),
        context_lines=a.get("context_lines", 0),
    ),
    "search_files": lambda a: search_files(
        a["query"], a.get("path", "."), a.get("max_results", 20)
    ),
    "inspect_html": lambda a: inspect_html(a["path"]),
    "preview_url": lambda a: preview_url(a.get("path", "index.html")),
    "todo_read": lambda a: todo_read(),
    "local_list": lambda a: local_list(a["path"]),
    "local_read": lambda a: local_read(a["path"]),
    "read_document": lambda a: read_document(a["path"]),
    "network_status": lambda a: network_status(),
    "web_fetch": lambda a: web_fetch(a["url"], a.get("max_chars", 4000)),
    "web_search": lambda a: web_search(a["query"], a.get("max_results", 5)),
    "computer_screenshot": lambda a: computer_screenshot(),
    "computer_ui_inspect": lambda a: computer_ui_inspect(a.get("app")),
    "computer_click": lambda a: computer_click(
        a.get("x", 0), a.get("y", 0), a.get("button", "left"), a.get("double", False)
    ),
    "computer_type": lambda a: computer_type(a["text"], a.get("interval", 0.04)),
    "computer_key": lambda a: computer_key(a["key"]),
    "computer_scroll": lambda a: computer_scroll(
        a.get("x", 0), a.get("y", 0), a.get("direction", "down"), a.get("clicks", 3)
    ),
    "computer_move": lambda a: computer_move(a.get("x", 0), a.get("y", 0)),
    "computer_drag": lambda a: computer_drag(
        a.get("x1", 0), a.get("y1", 0), a.get("x2", 0), a.get("y2", 0)
    ),
    "computer_status": lambda a: computer_status(),
    "chrome_status": lambda a: desktop_bridge_status(),
    "computer_use_status": lambda a: desktop_bridge_status(),
    "vision_analyze": lambda a: vision_analyze(
        a.get("image_b64", ""),
        a.get("prompt", "Describe this image in detail. Be concise."),
    ),
    "knowledge_search": lambda a: knowledge_search(
        a["query"], a.get("max_results", 5), **_knowledge_scope(a)
    ),
    "knowledge_tree": lambda a: knowledge_tree(**_knowledge_scope(a)),
    "obsidian_search": lambda a: obsidian_search(
        a["query"], a.get("max_results", 5), **_knowledge_scope(a)
    ),
    "obsidian_tree": lambda a: obsidian_tree(**_knowledge_scope(a)),
    "git_status": lambda a: git_status(a.get("cwd")),
    "git_diff": lambda a: git_diff(a.get("path"), a.get("cwd")),
    "git_log": lambda a: git_log(a.get("max_count", 5), a.get("cwd")),
    "git_show": lambda a: git_show(a.get("revision", "HEAD"), a.get("cwd")),
}


DEFAULT_TOOL_REGISTRY = ToolRegistry(TOOL_HANDLERS)


def registered_tools() -> frozenset:
    return DEFAULT_TOOL_REGISTRY.registered_tools()


def execute_tool(action: str, args: Dict[str, Any]) -> Dict[str, Any]:
    return DEFAULT_TOOL_REGISTRY.execute(action, args, error_cls=ToolError)


__all__ = [
    "AGENT_ROOT",
    "ToolError",
    "ensure_agent_root",
    "resolve_workspace_path",
    "list_dir",
    "workspace_tree",
    "read_file",
    "grep",
    "search_files",
    "inspect_html",
    "preview_url",
    "todo_read",
    "read_document",
    "document_output_target",
    "local_list",
    "local_read",
    "desktop_bridge_status",
    "knowledge_search",
    "knowledge_tree",
    "knowledge_scope_root",
    "obsidian_search",
    "obsidian_tree",
    "network_status",
    "web_fetch",
    "web_search",
    "validate_web_url",
    "distill_html_to_text",
    "sanitize_untrusted_web_text",
    "computer_screenshot",
    "computer_ui_inspect",
    "computer_click",
    "computer_type",
    "computer_key",
    "computer_scroll",
    "computer_move",
    "computer_drag",
    "computer_status",
    "vision_analyze",
    "git_status",
    "git_diff",
    "git_log",
    "git_show",
    "TOOL_HANDLERS",
    "DEFAULT_TOOL_REGISTRY",
    "registered_tools",
    "execute_tool",
    "BRAIN_DIR",
    "STRUCTURE",
]
