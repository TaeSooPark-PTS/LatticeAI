"""Central change-class governor for tool calls (v9.6.0).

The tool registry already answers "how risky is this tool?" (read / write /
exec, destructive, auto-approve). What it could not answer is the question
users actually care about: **does this call create something new, or does it
change/remove something that already exists?**

The governor adds that dimension in one place:

* ``read`` — no state change.
* ``additive`` — creates new content only (new file, new note, new node).
  Low friction: runs under the existing gates without extra ceremony.
* ``mutation`` — rewrites existing content (overwrite / edit of an existing
  file). Proposal-first: instead of applying silently, the change is staged
  as a review proposal the user merges deliberately.
* ``destructive`` — removes existing content. Always proposal-first.

Classification is deterministic and injectable (``path_exists``), so the
agent loop, the chat file path, and tests all share exactly one policy.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Mapping, Optional

CHANGE_READ = "read"
CHANGE_ADDITIVE = "additive"
CHANGE_MUTATION = "mutation"
CHANGE_DESTRUCTIVE = "destructive"
CHANGE_EXEC = "exec"

# Tools whose effect depends on whether the target already exists.
_TARGET_WRITE_TOOLS = frozenset({
    "write_file", "local_write",
    "create_docx", "create_xlsx", "create_pptx", "create_pdf",
})
# Tools that always rewrite existing content.
_ALWAYS_MUTATION_TOOLS = frozenset({"edit_file"})
# Tools that remove existing content.
_DESTRUCTIVE_TOOLS = frozenset({"delete_file", "remove_file", "clear_history"})
# Additive-only knowledge writes (append-style, never rewrite).
_ADDITIVE_TOOLS = frozenset({
    "knowledge_save", "obsidian_save", "todo_write", "create_web_project",
    "knowledge_graph_ingest",
})


def classify_tool_call(
    name: str,
    args: Mapping[str, Any],
    *,
    policy: Optional[Mapping[str, Any]] = None,
    path_exists: Optional[Callable[[str], bool]] = None,
) -> Dict[str, Any]:
    """Classify one tool call into a change class + proposal requirement."""
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
        reason = (
            "overwrites an existing file" if exists else "creates a new file"
        )
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

    return {
        "tool": name,
        "change_class": change,
        "proposal_required": change in {CHANGE_MUTATION, CHANGE_DESTRUCTIVE},
        "reason": reason,
    }


__all__ = [
    "CHANGE_READ",
    "CHANGE_ADDITIVE",
    "CHANGE_MUTATION",
    "CHANGE_DESTRUCTIVE",
    "CHANGE_EXEC",
    "classify_tool_call",
]
