"""Tool registry, governance, and dispatch helpers.

HTTP routers and the agent runtime share this service so policy checks and
tool-response shaping are owned outside ``server_app``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import subprocess
from typing import Any, Callable, Dict, Mapping, Optional

from fastapi import HTTPException

from latticeai.core.agent import AgentDeps, SingleAgentRuntime
from latticeai.core.agent_prompts import (
    CRITIC_PROMPT,
    EXECUTOR_PROMPT,
    MEMORY_UPDATER_PROMPT,
    PLANNER_PROMPT,
)
from latticeai.core.policy import role_has_capability
from latticeai.core.tool_governor import classify_tool_call
from latticeai.core.tool_registry import ToolPermission, ToolPolicy
from latticeai.tools import AGENT_ROOT, DEFAULT_TOOL_REGISTRY, ToolError, ensure_agent_root


def _default_load_users() -> Dict[str, Any]:
    return {}


def _default_get_user_role(_email, _users=None) -> str:
    return "user"


FILE_CREATE_ACTIONS = set(DEFAULT_TOOL_REGISTRY.file_create_actions)
TOOL_GOVERNANCE: Dict[str, ToolPolicy] = dict(DEFAULT_TOOL_REGISTRY.governance)
TOOL_GOVERNANCE_DEFAULT: ToolPolicy = DEFAULT_TOOL_REGISTRY.default_policy
ADMIN_ONLY_TOOLS: frozenset[str] = DEFAULT_TOOL_REGISTRY.admin_only_tools
EXPLICIT_CONSENT_TOOLS: frozenset[str] = frozenset({
    "local_list",
    "local_read",
    "local_write",
    "read_document",
})
LOCAL_WRITE_BLOCKED_PREFIXES = DEFAULT_TOOL_REGISTRY.local_write_blocked_prefixes
RISK_LEVEL_MAP = DEFAULT_TOOL_REGISTRY.risk_level_map


@dataclass
class ToolDispatchService:
    """Runtime-facing tool policy and authorization boundary.

    ``ToolRegistry`` owns the catalog and governance facts; this service owns
    request/runtime authorization callbacks that must be configured during app
    construction. Keeping those callbacks on an instance gives future runtime
    assembly code an injectable seam while the module-level functions below
    preserve the historical ``server_app`` surface.
    """

    registry: Any = field(default_factory=lambda: DEFAULT_TOOL_REGISTRY)
    load_users: Callable[[], Dict[str, Any]] = field(default=_default_load_users)
    get_user_role: Callable[..., str] = field(default=_default_get_user_role)

    @property
    def file_create_actions(self) -> frozenset[str]:
        return self.registry.file_create_actions

    @property
    def tool_governance(self) -> Mapping[str, ToolPolicy]:
        return self.registry.governance

    @property
    def risk_level_map(self) -> Mapping[str, str]:
        return self.registry.risk_level_map

    def configure(
        self,
        *,
        load_users: Callable[[], Dict[str, Any]],
        get_user_role: Callable[..., str],
    ) -> None:
        self.load_users = load_users
        self.get_user_role = get_user_role

    def policy_for(self, action_name: str, args: dict) -> ToolPolicy:
        return self.registry.policy_for(action_name, args)

    def risk_level(self, action_name: str, args: dict) -> str:
        return self.registry.risk_level(action_name, args)

    def risk_level_for_policy(self, policy: ToolPolicy) -> str:
        return self.risk_level_map.get(policy["risk"], "medium")

    def permission(self, name: str, args: Optional[dict] = None) -> ToolPermission:
        return self.registry.permission(name, args or {})

    def permissions(self) -> list[ToolPermission]:
        return self.registry.permissions()

    def diagnostics(self) -> Dict[str, Any]:
        return self.registry.diagnostics()

    def manifest(self) -> Dict[str, Any]:
        return self.registry.manifest()

    def check_role(self, tool_name: str, current_user: str) -> None:
        admin_only = tool_name in self.registry.admin_only_tools
        capability = self.policy_for(tool_name, {}).get("capability")
        if not admin_only and not capability:
            return
        users = self.load_users()
        role = str(self.get_user_role(current_user, users) or "user")
        if admin_only and role not in {"admin", "owner"}:
            raise HTTPException(
                status_code=403,
                detail=f"'{tool_name}' 툴은 관리자 전용입니다.",
            )
        if capability and not role_has_capability(role, capability):
            raise HTTPException(
                status_code=403,
                detail=f"'{tool_name}' 툴에는 '{capability}' capability가 필요합니다.",
            )

    def _governed_path_exists(self, path: str) -> bool:
        """Best-effort existence check for governance classification.

        Resolves workspace-relative paths under ``AGENT_ROOT`` and honors
        absolute paths (home-sandbox writes). Never raises — an unresolvable
        path is treated as non-existent (additive), which the fail-closed
        guard only escalates when the target actually exists.
        """
        try:
            candidate = Path(path)
            if not candidate.is_absolute():
                candidate = Path(AGENT_ROOT) / candidate
            return candidate.exists()
        except Exception:
            return False

    def user_role(self, current_user: str) -> str:
        users = self.load_users()
        try:
            return str(self.get_user_role(current_user, users) or "user")
        except Exception:
            return "user"

    def enforce_policy(
        self,
        tool_name: str,
        args: Optional[dict],
        *,
        current_user: str,
        source: str,
        require_auto_approval: bool = True,
        trusted_admin: bool = False,
    ) -> ToolPolicy:
        """Authorize a tool call before any hook or handler can execute.

        This is the shared policy gate for direct HTTP/MCP/tool surfaces. Agent
        runtime uses the same policy data and blocks non-auto-approved steps in
        its state machine so Computer Use direct endpoints can remain unchanged
        when explicitly excluded from a hardening pass.
        """
        policy = self.policy_for(tool_name, args or {})
        if source in {"mcp", "plugin"} and tool_name in EXPLICIT_CONSENT_TOOLS:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"'{tool_name}' requires the dedicated local-file approval flow "
                    "and cannot run through MCP or plugins."
                ),
            )
        if not trusted_admin:
            self.check_role(tool_name, current_user)
        if policy["destructive"] or policy["risk"] == "destructive":
            raise HTTPException(
                status_code=403,
                detail=f"'{tool_name}' 툴은 파괴적 작업으로 차단되었습니다.",
            )
        # Fail-closed governance: a call that would rewrite existing content but
        # cannot be staged as a reviewable proposal is blocked, never applied
        # silently. New-file (additive) calls are unaffected.
        verdict = classify_tool_call(
            tool_name, args or {},
            policy=dict(policy), path_exists=self._governed_path_exists,
        )
        if verdict.get("fail_closed"):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"'{tool_name}' 툴은 기존 콘텐츠를 변경하지만 검토 가능한 제안으로 "
                    "적용할 수 없어 차단되었습니다. 새 파일 이름으로 생성하거나 "
                    "지원되는 편집 도구(write_file/edit_file)를 사용하세요."
                ),
            )
        if (
            require_auto_approval
            and not trusted_admin
            and not policy["auto_approve"]
            and self.user_role(current_user) not in {"admin", "owner"}
        ):
            raise HTTPException(
                status_code=403,
                detail=(
                    f"'{tool_name}' 툴은 명시 승인이 필요합니다. "
                    "현재 릴리스에서는 승인 UI가 없는 직접 실행 경로에서 기본 차단됩니다."
                ),
            )
        return policy

    def rollback_file(self, path: str) -> Dict[str, Any]:
        r = subprocess.run(
            ["git", "checkout", "--", path],
            cwd=str(AGENT_ROOT),
            capture_output=True,
            text=True,
            timeout=10,
        )
        return {"path": path, "ok": r.returncode == 0, "stderr": r.stderr[:200]}


DEFAULT_TOOL_DISPATCH_SERVICE = ToolDispatchService()


def configure_tool_dispatch(
    *,
    load_users: Callable[[], Dict[str, Any]],
    get_user_role: Callable[..., str],
) -> None:
    DEFAULT_TOOL_DISPATCH_SERVICE.configure(
        load_users=load_users,
        get_user_role=get_user_role,
    )


def agent_policy(action_name: str, args: dict) -> ToolPolicy:
    return DEFAULT_TOOL_DISPATCH_SERVICE.policy_for(action_name, args)


def agent_risk(action_name: str, args: dict) -> str:
    return DEFAULT_TOOL_DISPATCH_SERVICE.risk_level(action_name, args)


def get_tool_permission(name: str, args: Optional[dict] = None) -> ToolPermission:
    return DEFAULT_TOOL_DISPATCH_SERVICE.permission(name, args or {})


def list_tool_permissions() -> list:
    return DEFAULT_TOOL_DISPATCH_SERVICE.permissions()


def tool_registry_diagnostics() -> Dict[str, Any]:
    return DEFAULT_TOOL_DISPATCH_SERVICE.diagnostics()


def tool_registry_manifest() -> Dict[str, Any]:
    return DEFAULT_TOOL_DISPATCH_SERVICE.manifest()


def check_tool_role(tool_name: str, current_user: str) -> None:
    DEFAULT_TOOL_DISPATCH_SERVICE.check_role(tool_name, current_user)


def enforce_tool_policy(
    tool_name: str,
    args: Optional[dict],
    *,
    current_user: str,
    source: str,
    require_auto_approval: bool = True,
    trusted_admin: bool = False,
) -> ToolPolicy:
    return DEFAULT_TOOL_DISPATCH_SERVICE.enforce_policy(
        tool_name,
        args or {},
        current_user=current_user,
        source=source,
        require_auto_approval=require_auto_approval,
        trusted_admin=trusted_admin,
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
    brain_memory: Any = None,
    dispatch_service: ToolDispatchService = DEFAULT_TOOL_DISPATCH_SERVICE,
) -> SingleAgentRuntime:
    ensure_agent_root()
    deps = AgentDeps(
        generate_as=model_router.generate_as,
        generate=model_router.generate,
        execute_tool=execute_tool,
        policy_for=dispatch_service.policy_for,
        risk_level=dispatch_service.risk_level_for_policy,
        check_role=dispatch_service.check_role,
        tool_governance=dispatch_service.tool_governance,
        file_create_actions=dispatch_service.file_create_actions,
        recent_chat_context=recent_chat_context,
        clear_history=clear_history,
        knowledge_save=knowledge_save,
        audit=audit,
        planner_prompt=PLANNER_PROMPT,
        executor_prompt=EXECUTOR_PROMPT,
        critic_prompt=CRITIC_PROMPT,
        memory_updater_prompt=MEMORY_UPDATER_PROMPT,
        agent_root=AGENT_ROOT,
        rollback_file=dispatch_service.rollback_file,
        hooks=hooks,
        brain_memory=brain_memory,
    )
    return SingleAgentRuntime(deps)


def tool_response(fn, *args):
    try:
        return {"status": "ok", "workspace": str(AGENT_ROOT), "result": fn(*args)}
    except ToolError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
