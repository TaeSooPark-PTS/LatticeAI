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

# ── Mutating-tool inventory (single source of truth) ─────────────────────
# Every tool with a side effect is classified into exactly one category so a
# CI check can prove nothing slips through ungoverned. Categories:
#   new_artifact         — only ever creates new content (additive)
#   existing_content_update — can rewrite content that already exists
#   delete               — removes existing content
#   external_side_effect — acts outside the Brain (shell, deploy, desktop, net)
#   internal_state       — agent-internal bookkeeping, not user content
NEW_ARTIFACT = "new_artifact"
EXISTING_CONTENT_UPDATE = "existing_content_update"
DELETE = "delete"
EXTERNAL_SIDE_EFFECT = "external_side_effect"
INTERNAL_STATE = "internal_state"

MUTATING_TOOL_INVENTORY: Dict[str, str] = {
    # text files — fully proposal-capable (staged + applied as reviewed)
    "write_file": EXISTING_CONTENT_UPDATE,
    "edit_file": EXISTING_CONTENT_UPDATE,
    # binary/document creators — can overwrite an existing file, but their
    # output cannot be staged as a text diff, so an overwrite is fail-closed.
    "create_docx": EXISTING_CONTENT_UPDATE,
    "create_xlsx": EXISTING_CONTENT_UPDATE,
    "create_pptx": EXISTING_CONTENT_UPDATE,
    "create_pdf": EXISTING_CONTENT_UPDATE,
    # home-sandbox write — same story: overwrite is fail-closed until the
    # proposal service learns to stage home-sandbox paths.
    "local_write": EXISTING_CONTENT_UPDATE,
    # additive-only writes
    "create_web_project": NEW_ARTIFACT,
    "knowledge_save": NEW_ARTIFACT,
    "obsidian_save": NEW_ARTIFACT,
    "knowledge_graph_ingest": NEW_ARTIFACT,
    # agent-internal state
    "todo_write": INTERNAL_STATE,
    # deletions
    "delete_file": DELETE,
    "remove_file": DELETE,
    "clear_history": DELETE,
    # external side effects (approval-gated, never proposal-based)
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

# Tools whose existing-content update the ChangeProposalService can actually
# stage AND apply as a reviewed proposal. A tool that is proposal_required but
# NOT here is fail-closed (blocked) rather than silently applied.
PROPOSAL_CAPABLE_TOOLS = frozenset({"write_file", "edit_file"})


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

    proposal_required = change in {CHANGE_MUTATION, CHANGE_DESTRUCTIVE}
    proposal_supported = name in PROPOSAL_CAPABLE_TOOLS
    # A change that must be reviewed as a proposal but that we cannot stage is
    # fail-closed: callers must block it instead of applying it silently.
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
    """Raise if any side-effecting registry tool is not classified.

    A new mutating tool added to the registry without an inventory entry makes
    this raise, so CI fails closed instead of shipping an ungoverned mutator.
    Read-only tools (risk ``read``) are intentionally exempt.
    """
    missing = [
        name for name in tool_names
        if name not in MUTATING_TOOL_INVENTORY
    ]
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
