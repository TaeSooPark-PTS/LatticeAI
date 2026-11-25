from __future__ import annotations

from typing import Any, Callable, Dict, Mapping, Optional

CHANGE_READ = "read"
CHANGE_ADDITIVE = "additive"
CHANGE_MUTATION = "mutation"
CHANGE_DESTRUCTIVE = "destructive"
CHANGE_EXEC = "exec"

_TARGET_WRITE_TOOLS = frozenset(
    {
        "write_file",
        "local_write",
        "create_docx",
        "create_xlsx",
        "create_pptx",
        "create_pdf",
    }
)
_ALWAYS_MUTATION_TOOLS = frozenset({"edit_file"})
_DESTRUCTIVE_TOOLS = frozenset({"delete_file", "remove_file", "clear_history"})
_ADDITIVE_TOOLS = frozenset(
    {
        "knowledge_save",
        "obsidian_save",
        "todo_write",
        "create_web_project",
        "knowledge_graph_ingest",
    }
)

NEW_ARTIFACT = "new_artifact"
EXISTING_CONTENT_UPDATE = "existing_content_update"
DELETE = "delete"
EXTERNAL_SIDE_EFFECT = "external_side_effect"
INTERNAL_STATE = "internal_state"

MUTATING_TOOL_INVENTORY: Dict[str, str] = {
    "write_file": EXISTING_CONTENT_UPDATE,
    "edit_file": EXISTING_CONTENT_UPDATE,
    "create_docx": EXISTING_CONTENT_UPDATE,
    "create_xlsx": EXISTING_CONTENT_UPDATE,
    "create_pptx": EXISTING_CONTENT_UPDATE,
    "create_pdf": EXISTING_CONTENT_UPDATE,
    "local_write": EXISTING_CONTENT_UPDATE,
    "create_web_project": NEW_ARTIFACT,
    "knowledge_save": NEW_ARTIFACT,
    "obsidian_save": NEW_ARTIFACT,
    "knowledge_graph_ingest": NEW_ARTIFACT,
    "todo_write": INTERNAL_STATE,
    "delete_file": DELETE,
    "remove_file": DELETE,
    "clear_history": DELETE,
    "run_command": EXTERNAL_SIDE_EFFECT,
    "build_project": EXTERNAL_SIDE_EFFECT,
    "deploy_project": EXTERNAL_SIDE_EFFECT,
    "computer_click": EXTERNAL_SIDE_EFFECT,
    "computer_type": EXTERNAL_SIDE_EFFECT,
    "computer_key": EXTERNAL_SIDE_EFFECT,
    "computer_scroll": EXTERNAL_SIDE_EFFECT,
    "computer_drag": EXTERNAL_SIDE_EFFECT,
    "computer_move": EXTERNAL_SIDE_EFFECT,
    "computer_open_app": EXTERNAL_SIDE_EFFECT,
    "computer_open_url": EXTERNAL_SIDE_EFFECT,
}

PROPOSAL_CAPABLE_TOOLS = frozenset({"write_file", "edit_file"})


def classify_tool_call(
    name: str,
    args: Mapping[str, Any],
    *,
    policy: Optional[Mapping[str, Any]] = None,
    path_exists: Optional[Callable[[str], bool]] = None,
) -> Dict[str, Any]:
    risk = str((policy or {}).get("risk") or "")
    change = CHANGE_READ
    reason = "read-only tool"

    if name in _DESTRUCTIVE_TOOLS or (policy or {}).get("destructive"):
        change = CHANGE_DESTRUCTIVE
        reason = "removes existing content"
    elif name in _ALWAYS_MUTATION_TOOLS:
        change = CHANGE_MUTATION
        reason = "edits existing content in place"
    elif name in _TARGET_WRITE_TOOLS:
        path = str(args.get("path") or args.get("filename") or "")
        exists = bool(path and path_exists and path_exists(path))
        change = CHANGE_MUTATION if exists else CHANGE_ADDITIVE
        reason = "overwrites an existing file" if exists else "creates a new file"
    elif name in _ADDITIVE_TOOLS:
        change = CHANGE_ADDITIVE
        reason = "adds new content only"
    elif risk.startswith("read"):
        change = CHANGE_READ
        reason = "read-only tool"
    elif risk == "exec":
        change = CHANGE_EXEC
        reason = "executes an action (approval-gated, not proposal-based)"
    elif risk in {"write", "write_scoped"}:
        change = CHANGE_ADDITIVE
        reason = "write tool without an existing target"

    proposal_required = change in {CHANGE_MUTATION, CHANGE_DESTRUCTIVE}
    proposal_supported = name in PROPOSAL_CAPABLE_TOOLS
    fail_closed = proposal_required and not proposal_supported

    return {
        "tool": name,
        "change_class": change,
        "proposal_required": proposal_required,
        "proposal_supported": proposal_supported,
        "fail_closed": fail_closed,
        "reason": reason,
    }


def assert_governance_coverage(tool_names: "Any") -> None:
    missing = [name for name in tool_names if name not in MUTATING_TOOL_INVENTORY]
    if missing:
        raise ValueError(
            "ungoverned mutating tools (add to MUTATING_TOOL_INVENTORY): "
            + ", ".join(sorted(missing))
        )


__all__ = [
    "CHANGE_READ",
    "CHANGE_ADDITIVE",
    "CHANGE_MUTATION",
    "CHANGE_DESTRUCTIVE",
    "CHANGE_EXEC",
    "NEW_ARTIFACT",
    "EXISTING_CONTENT_UPDATE",
    "DELETE",
    "EXTERNAL_SIDE_EFFECT",
    "INTERNAL_STATE",
    "MUTATING_TOOL_INVENTORY",
    "PROPOSAL_CAPABLE_TOOLS",
    "classify_tool_call",
    "assert_governance_coverage",
]
