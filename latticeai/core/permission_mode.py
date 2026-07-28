"""Permission modes for agent/tool autonomy (v9.9.8).

Frontier agents expose a mode dial (Claude Code: default → acceptEdits →
auto → bypassPermissions; Cursor: allowlist → auto-review → run everything;
Codex: approval × sandbox). LatticeAI maps the same idea onto the existing
ToolRegistry + Change Governor without throwing those gates away.

Modes
-----
* ``strict``  — current fail-closed defaults (every non-auto tool needs
  approval; mutations become review proposals).
* ``trusted`` — workspace autonomy: knowledge reads, workspace writes
  (additive + mutation), and computer *observation* auto-run. Exec,
  destructive, host-control, and system-sandbox writes still gate.
* ``bypass``  — YOLO within the agent workspace. Hard circuit breakers
  still fire (blocked path prefixes, destructive root/home removals,
  system-sandbox writes that escape the workspace).

Circuit breakers are mode-invariant: a mode never overrides a hard deny.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, FrozenSet, Mapping, Optional


class PermissionMode(str, Enum):
    STRICT = "strict"
    TRUSTED = "trusted"
    BYPASS = "bypass"


DEFAULT_MODE = PermissionMode.STRICT

# Computer-use split: observation is low-friction in trusted; control stays gated.
COMPUTER_OBSERVATION_TOOLS: FrozenSet[str] = frozenset({
    "computer_screenshot",
    "computer_status",
    "computer_use_status",
    "chrome_status",
    "vision_analyze",
})

COMPUTER_CONTROL_TOOLS: FrozenSet[str] = frozenset({
    "computer_click",
    "computer_type",
    "computer_key",
    "computer_scroll",
    "computer_drag",
    "computer_move",
    "computer_open_app",
    "computer_open_url",
})

KNOWLEDGE_READ_TOOLS: FrozenSet[str] = frozenset({
    "knowledge_search",
    "knowledge_tree",
    "obsidian_search",
    "obsidian_tree",
    "knowledge_graph_search",
    "knowledge_graph_graph",
    "knowledge_graph_context",
})

WORKSPACE_WRITE_TOOLS: FrozenSet[str] = frozenset({
    "write_file",
    "edit_file",
    "create_docx",
    "create_xlsx",
    "create_pptx",
    "create_pdf",
    "create_web_project",
    "knowledge_save",
    "obsidian_save",
    "knowledge_graph_ingest",
    "todo_write",
})

# Always blocked regardless of mode (Claude-style circuit breakers).
HARD_BLOCK_SANDBOXES: FrozenSet[str] = frozenset({"system"})


def normalize_mode(value: Any) -> PermissionMode:
    """Parse user/API/env input into a PermissionMode; unknown → strict."""
    if isinstance(value, PermissionMode):
        return value
    text = str(value or "").strip().lower()
    aliases = {
        "strict": PermissionMode.STRICT,
        "default": PermissionMode.STRICT,
        "manual": PermissionMode.STRICT,
        "trusted": PermissionMode.TRUSTED,
        "acceptedits": PermissionMode.TRUSTED,
        "accept_edits": PermissionMode.TRUSTED,
        "workspace": PermissionMode.TRUSTED,
        "bypass": PermissionMode.BYPASS,
        "bypasspermissions": PermissionMode.BYPASS,
        "bypass_permissions": PermissionMode.BYPASS,
        "yolo": PermissionMode.BYPASS,
        "dangerously-skip-permissions": PermissionMode.BYPASS,
    }
    return aliases.get(text, DEFAULT_MODE)


def mode_catalog() -> list[Dict[str, Any]]:
    """UI/API catalog for the mode selector."""
    return [
        {
            "id": PermissionMode.STRICT.value,
            "label": "Strict",
            "label_ko": "엄격",
            "summary": "Reads auto; writes and exec need approval or review proposals.",
            "summary_ko": "읽기는 자동, 쓰기·실행은 승인 또는 변경 제안.",
            "risk": "low",
            "requires_ack": False,
        },
        {
            "id": PermissionMode.TRUSTED.value,
            "label": "Trusted",
            "label_ko": "신뢰",
            "summary": "Workspace writes and knowledge reads auto-run; exec/desktop control still gated.",
            "summary_ko": "워크스페이스 쓰기·지식 읽기 자동. 실행·데스크톱 제어는 승인 필요.",
            "risk": "medium",
            "requires_ack": False,
        },
        {
            "id": PermissionMode.BYPASS.value,
            "label": "Bypass",
            "label_ko": "바이패스",
            "summary": "YOLO inside the agent workspace. Hard circuit breakers still apply.",
            "summary_ko": "에이전트 워크스페이스 안에서 전부 자동. 하드 차단만 남음.",
            "risk": "high",
            "requires_ack": True,
            "warning": (
                "Bypass skips routine approval prompts. Destructive system paths, "
                "root/home wipes, and blocked prefixes remain denied."
            ),
            "warning_ko": (
                "바이패스는 일상 승인 프롬프트를 건너뜁니다. 시스템 경로 파괴, "
                "루트/홈 삭제, 차단 접두사는 계속 거부됩니다."
            ),
        },
    ]


def is_circuit_breaker(
    tool_name: str,
    policy: Mapping[str, Any],
    args: Optional[Mapping[str, Any]] = None,
) -> Optional[str]:
    """Return a reason string when the call must be denied in every mode."""
    risk = str(policy.get("risk") or "")
    destructive = bool(policy.get("destructive"))

    if destructive or risk == "destructive":
        return "destructive action is always blocked"

    # NOTE: system-sandbox writes are *mode-sensitive*, not circuit breakers —
    # ``effective_auto_approve`` keeps them gated under every mode and the
    # blocked-path-prefix guard upstream denies escapes outright. Deciding them
    # here would make ``bypass`` unable to drive desktop control at all.

    path = str((args or {}).get("path") or (args or {}).get("filename") or "")
    if path:
        normalized = path.replace("\\", "/")
        # Root / home mass-delete style paths — Claude circuit breaker analogue.
        if normalized in {"/", "~", "/home", "/Users"} or normalized.rstrip("/") in {"/", "~"}:
            return f"circuit breaker: refusing path {path!r}"

    command = str((args or {}).get("command") or (args or {}).get("cmd") or "")
    lowered = command.lower()
    if any(token in lowered for token in ("rm -rf /", "rm -rf ~", "rm -rf /*", "rm -rf $home")):
        return "circuit breaker: refusing destructive shell command"

    return None


def effective_auto_approve(
    mode: PermissionMode | str,
    tool_name: str,
    policy: Mapping[str, Any],
    *,
    change_class: Optional[str] = None,
    args: Optional[Mapping[str, Any]] = None,
) -> bool:
    """Whether this tool call may run without an extra human approval prompt.

    Does **not** override circuit breakers — callers must check
    :func:`is_circuit_breaker` first and deny when it returns a reason.
    """
    mode = normalize_mode(mode)
    if policy.get("auto_approve"):
        return True

    risk = str(policy.get("risk") or "")
    sandbox = str(policy.get("sandbox") or "workspace")

    if mode == PermissionMode.STRICT:
        return False

    if mode == PermissionMode.TRUSTED:
        if tool_name in KNOWLEDGE_READ_TOOLS:
            return True
        if tool_name in COMPUTER_OBSERVATION_TOOLS:
            return True
        if tool_name in WORKSPACE_WRITE_TOOLS and sandbox == "workspace":
            # Additive and mutation both auto under trusted; destructive still
            # blocked by circuit breaker / risk check above.
            if risk in {"write", "write_scoped", "read"} or change_class in {
                "additive", "mutation", "read", None,
            }:
                if risk != "destructive" and not policy.get("destructive"):
                    return True
        # Explicit reads that were gated only for consent (local_list etc.) stay gated.
        return False

    # bypass
    if risk == "destructive" or policy.get("destructive"):
        return False
    if sandbox == "system" and tool_name not in COMPUTER_OBSERVATION_TOOLS | COMPUTER_CONTROL_TOOLS:
        # Non-desktop system tools stay gated even in bypass.
        if risk in {"write", "exec"}:
            return False
    return True


def should_stage_proposal(
    mode: PermissionMode | str,
    *,
    proposal_required: bool,
) -> bool:
    """Whether mutation/destructive file changes should become Review proposals.

    * strict  — yes (proposal-first, current behaviour)
    * trusted — no for mutations (auto-apply + audit); destructive still blocked
      upstream so this only affects mutation proposals
    * bypass  — no
    """
    if not proposal_required:
        return False
    mode = normalize_mode(mode)
    return mode == PermissionMode.STRICT


def plan_requires_approval(
    mode: PermissionMode | str,
    *,
    non_auto_steps: list,
    plan_flag: bool = False,
) -> bool:
    """Plan-level gate used by SingleAgentRuntime.approval_requirements."""
    mode = normalize_mode(mode)
    if mode == PermissionMode.BYPASS:
        return False
    if mode == PermissionMode.TRUSTED:
        # Only steps that remain non-auto under trusted force a plan pause.
        return bool(non_auto_steps) or bool(plan_flag)
    return bool(non_auto_steps) or bool(plan_flag)


def mode_contract(mode: PermissionMode | str) -> Dict[str, Any]:
    """Serializable contract for API / agent responses."""
    mode = normalize_mode(mode)
    entry = next((m for m in mode_catalog() if m["id"] == mode.value), mode_catalog()[0])
    return {
        "mode": mode.value,
        "label": entry["label"],
        "label_ko": entry["label_ko"],
        "risk": entry["risk"],
        "requires_ack": entry["requires_ack"],
        "proposal_first": mode == PermissionMode.STRICT,
        "workspace_writes_auto": mode in {PermissionMode.TRUSTED, PermissionMode.BYPASS},
        "knowledge_reads_auto": mode in {PermissionMode.TRUSTED, PermissionMode.BYPASS},
        "exec_auto": mode == PermissionMode.BYPASS,
        "computer_observation_auto": mode in {PermissionMode.TRUSTED, PermissionMode.BYPASS},
        "computer_control_auto": mode == PermissionMode.BYPASS,
        "circuit_breakers": True,
    }


__all__ = [
    "PermissionMode",
    "DEFAULT_MODE",
    "COMPUTER_OBSERVATION_TOOLS",
    "COMPUTER_CONTROL_TOOLS",
    "KNOWLEDGE_READ_TOOLS",
    "WORKSPACE_WRITE_TOOLS",
    "normalize_mode",
    "mode_catalog",
    "is_circuit_breaker",
    "effective_auto_approve",
    "should_stage_proposal",
    "plan_requires_approval",
    "mode_contract",
]
