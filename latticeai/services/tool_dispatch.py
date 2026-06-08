"""Tool registry, governance, and dispatch helpers.

HTTP routers and the agent runtime share this service so policy checks and
tool-response shaping are owned outside ``server_app``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, Optional

from fastapi import HTTPException

from latticeai.core.agent import AgentDeps, AgentRuntime
from latticeai.core.agent_prompts import (
    CRITIC_PROMPT,
    EXECUTOR_PROMPT,
    MEMORY_UPDATER_PROMPT,
    PLANNER_PROMPT,
)
from latticeai.core.tool_registry import ToolPermission, ToolPolicy
from tools import AGENT_ROOT, DEFAULT_TOOL_REGISTRY, ToolError, ensure_agent_root


_load_users: Callable[[], Dict[str, Any]] = lambda: {}
_get_user_role: Callable[..., str] = lambda _email, _users=None: "user"

FILE_CREATE_ACTIONS = set(DEFAULT_TOOL_REGISTRY.file_create_actions)
TOOL_GOVERNANCE: Dict[str, ToolPolicy] = dict(DEFAULT_TOOL_REGISTRY.governance)
TOOL_GOVERNANCE_DEFAULT: ToolPolicy = DEFAULT_TOOL_REGISTRY.default_policy
ADMIN_ONLY_TOOLS: frozenset[str] = DEFAULT_TOOL_REGISTRY.admin_only_tools
LOCAL_WRITE_BLOCKED_PREFIXES = DEFAULT_TOOL_REGISTRY.local_write_blocked_prefixes
RISK_LEVEL_MAP = DEFAULT_TOOL_REGISTRY.risk_level_map


def configure_tool_dispatch(
    *,
    load_users: Callable[[], Dict[str, Any]],
    get_user_role: Callable[..., str],
) -> None:
    global _load_users, _get_user_role
    _load_users = load_users
    _get_user_role = get_user_role


def agent_policy(action_name: str, args: dict) -> ToolPolicy:
    return DEFAULT_TOOL_REGISTRY.policy_for(action_name, args)


def agent_risk(action_name: str, args: dict) -> str:
    return DEFAULT_TOOL_REGISTRY.risk_level(action_name, args)


def get_tool_permission(name: str, args: Optional[dict] = None) -> ToolPermission:
    return DEFAULT_TOOL_REGISTRY.permission(name, args or {})


def list_tool_permissions() -> list:
    return DEFAULT_TOOL_REGISTRY.permissions()


def check_tool_role(tool_name: str, current_user: str) -> None:
    if tool_name not in ADMIN_ONLY_TOOLS:
        return
    users = _load_users()
    if _get_user_role(current_user, users) != "admin":
        raise HTTPException(
            status_code=403,
            detail=f"'{tool_name}' 툴은 관리자 전용입니다.",
        )


def collect_created_files(transcript: list) -> list:
    files = []
    for step in transcript:
        if step.get("action") in FILE_CREATE_ACTIONS:
            result = step.get("result", {})
            if isinstance(result.get("created_files"), list):
                for rel_path in result["created_files"]:
                    files.append({
                        "path": rel_path,
                        "filename": Path(rel_path).name,
                        "bytes": 0,
                        "action": step["action"],
                    })
                continue
            path = result.get("path")
            if path:
                files.append({
                    "path": path,
                    "filename": Path(path).name,
                    "bytes": result.get("bytes", 0),
                    "action": step["action"],
                })
    return files


def build_agent_runtime(
    *,
    model_router: Any,
    execute_tool: Callable[..., Dict[str, Any]],
    recent_chat_context: Callable[..., str],
    clear_history: Callable[[int], Dict[str, Any]],
    knowledge_save: Callable[..., Dict[str, Any]],
    audit: Callable[..., None],
    hooks: Any = None,
) -> AgentRuntime:
    ensure_agent_root()
    deps = AgentDeps(
        generate_as=model_router.generate_as,
        generate=model_router.generate,
        execute_tool=execute_tool,
        policy_for=agent_policy,
        risk_level=lambda policy: RISK_LEVEL_MAP.get(policy["risk"], "medium"),
        check_role=check_tool_role,
        tool_governance=TOOL_GOVERNANCE,
        file_create_actions=frozenset(FILE_CREATE_ACTIONS),
        recent_chat_context=recent_chat_context,
        clear_history=clear_history,
        knowledge_save=knowledge_save,
        audit=audit,
        planner_prompt=PLANNER_PROMPT,
        executor_prompt=EXECUTOR_PROMPT,
        critic_prompt=CRITIC_PROMPT,
        memory_updater_prompt=MEMORY_UPDATER_PROMPT,
        agent_root=AGENT_ROOT,
        hooks=hooks,
    )
    return AgentRuntime(deps)


def tool_response(fn, *args):
    try:
        return {"status": "ok", "workspace": str(AGENT_ROOT), "result": fn(*args)}
    except ToolError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

