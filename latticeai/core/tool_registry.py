"""Tool dispatch, governance, and catalog metadata.

The registry is the single ownership point for tool names: one object exposes
dispatch, policy lookup, prompt catalog text, MCP descriptions, and permission
views. The actual tool functions still live in the top-level ``tools`` module
to preserve the public API and keep this module free of filesystem side
effects at import time.
"""

from __future__ import annotations

__all__ = [
    "ToolPolicy",
    "ToolPermission",
    "TOOL_CATALOG_BRIEF",
    "FILE_CREATE_ACTIONS",
    "LOCAL_WRITE_BLOCKED_PREFIXES",
    "RISK_LEVEL_MAP",
    "ToolRegistry",
]

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Mapping, Optional, TypedDict


class ToolPolicy(TypedDict):
    risk: str
    destructive: bool
    shell: bool
    network: bool
    auto_approve: bool
    sandbox: str
    rollback: str


class ToolPermission(TypedDict):
    tool: str
    risk: str
    requires_approval: bool
    network: bool


TOOL_CATALOG_BRIEF = """
FILESYSTEM  : list_dir  workspace_tree  read_file  write_file  edit_file  grep  search_files  inspect_html  preview_url
PLANNING    : todo_read  todo_write
PROJECT     : run_command  build_project  deploy_project  create_web_project
GIT (read)  : git_status  git_diff  git_log  git_show
LOCAL FS    : local_list  local_read  local_write  read_document
DOCS        : create_docx  create_xlsx  create_pptx  create_pdf
KNOWLEDGE   : knowledge_save  knowledge_search  knowledge_tree
COMPUTER    : computer_screenshot  computer_open_app  computer_open_url  computer_click  computer_type  computer_key
MISC        : network_status  clear_history  final
"""

FILE_CREATE_ACTIONS = frozenset({
    "create_docx",
    "create_xlsx",
    "create_pptx",
    "create_pdf",
    "write_file",
    "edit_file",
    "create_web_project",
})

LOCAL_WRITE_BLOCKED_PREFIXES = (
    "/etc/",
    "/usr/",
    "/bin/",
    "/sbin/",
    "/System/",
    "/private/etc/",
    "/Library/LaunchDaemons/",
    "/Library/LaunchAgents/",
)

RISK_LEVEL_MAP = {
    "read": "low",
    "write": "medium",
    "exec": "high",
    "destructive": "high",
}


def _r(sandbox: str = "workspace", rollback: str = "none") -> ToolPolicy:
    return ToolPolicy(
        risk="read", destructive=False, shell=False, network=False,
        auto_approve=True, sandbox=sandbox, rollback=rollback,
    )


def _rs(sandbox: str = "workspace", rollback: str = "none") -> ToolPolicy:
    return ToolPolicy(
        risk="read", destructive=False, shell=True, network=False,
        auto_approve=True, sandbox=sandbox, rollback=rollback,
    )


def _rn(sandbox: str = "system", rollback: str = "none") -> ToolPolicy:
    return ToolPolicy(
        risk="read", destructive=False, shell=True, network=True,
        auto_approve=True, sandbox=sandbox, rollback=rollback,
    )


def _w(sandbox: str = "workspace", rollback: str = "none") -> ToolPolicy:
    return ToolPolicy(
        risk="write", destructive=False, shell=False, network=False,
        auto_approve=False, sandbox=sandbox, rollback=rollback,
    )


def _wa(sandbox: str = "workspace", rollback: str = "none") -> ToolPolicy:
    return ToolPolicy(
        risk="write", destructive=False, shell=False, network=False,
        auto_approve=True, sandbox=sandbox, rollback=rollback,
    )


def _e(sandbox: str = "workspace", rollback: str = "none") -> ToolPolicy:
    return ToolPolicy(
        risk="exec", destructive=False, shell=True, network=False,
        auto_approve=False, sandbox=sandbox, rollback=rollback,
    )


def _en(sandbox: str = "workspace", rollback: str = "none") -> ToolPolicy:
    return ToolPolicy(
        risk="exec", destructive=False, shell=True, network=True,
        auto_approve=False, sandbox=sandbox, rollback=rollback,
    )


def _ec(sandbox: str = "system", rollback: str = "none") -> ToolPolicy:
    return ToolPolicy(
        risk="exec", destructive=False, shell=False, network=False,
        auto_approve=False, sandbox=sandbox, rollback=rollback,
    )


TOOL_GOVERNANCE: Dict[str, ToolPolicy] = {
    "list_dir": _r(),
    "workspace_tree": _r(),
    "read_file": _r(),
    "search_files": _r(),
    "grep": _r(),
    "inspect_html": _r(),
    "preview_url": _r(),
    "todo_read": _r(),
    "local_list": _r(sandbox="home"),
    "local_read": _r(sandbox="home"),
    "read_document": _r(sandbox="home"),
    "git_status": _rs(),
    "git_diff": _rs(),
    "git_log": _rs(),
    "git_show": _rs(),
    "knowledge_search": _r(sandbox="home"),
    "knowledge_tree": _r(sandbox="home"),
    "obsidian_search": _r(sandbox="home"),
    "obsidian_tree": _r(sandbox="home"),
    "computer_screenshot": _r(sandbox="system"),
    "computer_status": _r(sandbox="system"),
    "chrome_status": _r(sandbox="system"),
    "computer_use_status": _r(sandbox="system"),
    "network_status": _rn(),
    "write_file": _w(rollback="git"),
    "edit_file": _w(rollback="git"),
    "create_web_project": _w(),
    "create_docx": _w(),
    "create_xlsx": _w(),
    "create_pptx": _w(),
    "create_pdf": _w(),
    "todo_write": _w(),
    "knowledge_save": _w(sandbox="home"),
    "obsidian_save": _w(sandbox="home"),
    "local_write": _w(sandbox="home"),
    "run_command": _e(),
    "build_project": _e(),
    "deploy_project": _en(),
    "computer_click": _ec(),
    "computer_type": _ec(),
    "computer_key": _ec(),
    "computer_scroll": _ec(),
    "computer_drag": _ec(),
    "computer_move": _ec(),
    "computer_open_app": _ec(),
    "computer_open_url": ToolPolicy(
        risk="exec", destructive=False, shell=False, network=True,
        auto_approve=False, sandbox="system", rollback="none",
    ),
    "vision_analyze": _r(sandbox="system"),
}

TOOL_GOVERNANCE_DEFAULT = ToolPolicy(
    risk="write", destructive=False, shell=False, network=False,
    auto_approve=False, sandbox="workspace", rollback="none",
)

MCP_TOOL_DESCRIPTIONS: Dict[str, str] = {
    "list_dir": "List files in the agent workspace.",
    "workspace_tree": "Return a recursive workspace tree.",
    "read_file": "Read a UTF-8 file from the workspace with optional line numbers and offset/limit slicing.",
    "write_file": "Write a UTF-8 file inside the workspace (new files / full rewrites).",
    "edit_file": "Precise diff-style edit: replace exact old_string with new_string. Requires unique match unless replace_all=true.",
    "search_files": "Substring search in text files (legacy).",
    "grep": "Regex search across the workspace with line numbers and optional context.",
    "todo_read": "Read the agent's persistent TODO list for the current workspace.",
    "todo_write": "Replace the agent's TODO list (id, content, status: pending/in_progress/completed).",
    "clear_history": "Clear chat history to reduce context and speed up responses.",
    "inspect_html": "Inspect local HTML structure and assets.",
    "preview_url": "Return a server URL for a workspace file.",
    "create_web_project": "Create a web project scaffold inside the workspace.",
    "create_docx": "Create a Word DOCX document in the agent workspace.",
    "create_xlsx": "Create an XLSX spreadsheet in the agent workspace.",
    "create_pptx": "Create a PPTX presentation deck in the agent workspace.",
    "create_pdf": "Create a PDF document in the agent workspace.",
    "local_list": "List any local folder (requires user permission via UI).",
    "local_read": "Read any local file (requires user permission via UI).",
    "local_write": "Write any local file (requires user permission via UI).",
    "read_document": "Extract text from PDF, DOCX, XLSX, PPTX, TXT, MD, CSV files.",
    "computer_screenshot": "Capture the current Mac screen as base64 PNG.",
    "computer_open_app": "Open or focus a Mac app, e.g. Google Chrome.",
    "computer_open_url": "Open a URL in a Mac app, e.g. Google Chrome.",
    "computer_click": "Click at screen coordinates (x, y).",
    "computer_type": "Type text at the current focus position.",
    "computer_key": "Press a keyboard key or shortcut (e.g. 'command+c').",
    "computer_scroll": "Scroll at screen coordinates.",
    "computer_move": "Move the mouse to screen coordinates.",
    "computer_drag": "Drag from (x1,y1) to (x2,y2).",
    "computer_status": "Check if Mac desktop control (pyautogui) is available.",
    "chrome_status": "Report Chrome desktop bridge availability.",
    "computer_use_status": "Report Mac desktop-control bridge availability.",
    "vision_analyze": "Analyze a base64-encoded image (e.g. screenshot) using the active multimodal VLM. Returns structured description for agent consumption.",
    "knowledge_save": "Save a note into the local knowledge garden.",
    "knowledge_search": "Search the local knowledge garden.",
    "knowledge_tree": "List local knowledge garden markdown files.",
    "knowledge_graph_ingest": "Ingest a message, AI answer, or connector event into the SQLite knowledge graph.",
    "knowledge_graph_search": "Search graph nodes, summaries, and JSON metadata.",
    "knowledge_graph_graph": "Return Obsidian-style graph nodes and edges.",
    "knowledge_graph_context": "Return compact graph-backed RAG context for a prompt.",
    "obsidian_save": "Save a note into the Obsidian-compatible memory vault.",
    "obsidian_search": "Search the Obsidian-compatible memory vault.",
    "obsidian_tree": "List Obsidian memory vault markdown files.",
    "git_status": "Read-only local git status inside the workspace.",
    "git_diff": "Read-only local git diff inside the workspace.",
    "git_log": "Read-only local git log inside the workspace.",
    "git_show": "Read-only local git show --stat inside the workspace.",
    "network_status": "Get current local/private IP, public IP, hostname, and Wi-Fi info.",
    "run_command": "Run an allowlisted local command inside the workspace.",
    "build_project": "Run an allowlisted package.json build/compile/typecheck/test script to verify changes actually work.",
    "deploy_project": "Run an allowlisted package.json deploy/preview/release/package installer script (pkg/exe).",
}


@dataclass
class ToolRegistry:
    handlers: Mapping[str, Callable[[Dict[str, Any]], Dict[str, Any]]]
    governance: Mapping[str, ToolPolicy] = field(default_factory=lambda: TOOL_GOVERNANCE)
    default_policy: ToolPolicy = field(default_factory=lambda: TOOL_GOVERNANCE_DEFAULT)
    descriptions: Mapping[str, str] = field(default_factory=lambda: MCP_TOOL_DESCRIPTIONS)
    catalog_brief: str = TOOL_CATALOG_BRIEF
    file_create_actions: frozenset[str] = FILE_CREATE_ACTIONS
    local_write_blocked_prefixes: tuple[str, ...] = LOCAL_WRITE_BLOCKED_PREFIXES
    risk_level_map: Mapping[str, str] = field(default_factory=lambda: RISK_LEVEL_MAP)

    @property
    def admin_only_tools(self) -> frozenset[str]:
        return frozenset(
            name for name, policy in self.governance.items()
            if policy["sandbox"] == "system" or policy["risk"] in {"exec", "destructive"}
        )

    def registered_tools(self) -> frozenset[str]:
        return frozenset(self.handlers)

    def diagnostics(self) -> Dict[str, Any]:
        """Return registry drift checks without executing any tool.

        This is intentionally derived from the live registry instance, not from
        duplicated constants, so admin/runtime surfaces can prove whether the
        dispatch, governance, and catalog projections are still aligned.
        """

        registered = set(self.registered_tools())
        governed = set(self.governance)
        described = set(self.descriptions)
        return {
            "ready": not (governed - registered or registered - governed or registered - described),
            "registered_tools": len(registered),
            "governed_tools": len(governed),
            "described_tools": len(described),
            "governance_without_handler": sorted(governed - registered),
            "handler_without_governance": sorted(registered - governed),
            "handler_without_description": sorted(registered - described),
            "description_without_handler": sorted(described - registered),
        }

    def manifest(self) -> Dict[str, Any]:
        """Serializable tool registry contract for runtime/admin inspection."""

        tools = []
        for name in sorted(self.registered_tools() | set(self.governance) | set(self.descriptions)):
            policy = self.policy_for(name, {})
            tools.append({
                "name": name,
                "registered": name in self.handlers,
                "governed": name in self.governance,
                "described": name in self.descriptions,
                "description": self.descriptions.get(name, ""),
                "policy": dict(policy),
                "permission": dict(self.permission(name, {})),
            })
        diagnostics = self.diagnostics()
        return {
            "schema_version": "tool-registry-contract/v1",
            "status": "ok" if diagnostics["ready"] else "degraded",
            "boundary": {
                "owner": "latticeai.core.tool_registry.ToolRegistry",
                "dispatch_owner": "tools.DEFAULT_TOOL_REGISTRY",
                "policy_owner": "latticeai.core.tool_registry.ToolRegistry",
                "permission_owner": "latticeai.services.tool_dispatch.ToolDispatchService",
            },
            "catalog_brief": self.catalog_brief.strip(),
            "diagnostics": diagnostics,
            "tools": tools,
        }

    def execute(self, action: str, args: Dict[str, Any], *, error_cls: type[Exception]) -> Dict[str, Any]:
        handler = self.handlers.get(action)
        if handler is None:
            raise error_cls(f"Unknown action: {action}")
        return handler(args or {})

    def policy_for(self, action_name: str, args: Optional[dict] = None) -> ToolPolicy:
        policy = self.governance.get(action_name, self.default_policy)
        if action_name in {"local_write", "write_file", "edit_file"}:
            path = str((args or {}).get("path", "")).replace("\\", "/")
            for prefix in self.local_write_blocked_prefixes:
                normalized_prefix = str(prefix).rstrip("/")
                blocked = path == normalized_prefix or path.startswith(f"{normalized_prefix}/")
                if not blocked:
                    continue
                return ToolPolicy(
                    risk="destructive", destructive=True, shell=False, network=False,
                    auto_approve=False, sandbox="system", rollback="none",
                )
        return policy

    def risk_level(self, policy_or_action: ToolPolicy | str, args: Optional[dict] = None) -> str:
        if isinstance(policy_or_action, str):
            policy = self.policy_for(policy_or_action, args or {})
        else:
            policy = policy_or_action
        return self.risk_level_map.get(policy["risk"], "medium")

    def permission(self, name: str, args: Optional[dict] = None) -> ToolPermission:
        policy = self.policy_for(name, args or {})
        return ToolPermission(
            tool=name,
            risk=self.risk_level(policy),
            requires_approval=not policy["auto_approve"],
            network=policy["network"],
        )

    def permissions(self) -> list[ToolPermission]:
        return [self.permission(name) for name in sorted(self.governance.keys())]
