"""v2.0 Agentic Workspace Platform runtime — cross-system wiring.

This is the single place the four v2.0 subsystems (Plugin SDK, Workflow
Designer, Multi-Agent Runtime, Realtime) connect to one another and to the
workspace. Keeping it out of ``server_app`` honours the AGENTS.md preference for
small, composable modules and keeps the wiring independently testable.

Recursion is bounded by construction: a workflow's ``agent`` node runs an
orchestrator *without* a workflow runner, and an orchestrator's workflow runner
runs an engine *without* an ``agent`` runner — so the deepest chains are
``agent → workflow → (tool|skill|plugin)`` and ``workflow → agent → plugin``.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Set

from fastapi import HTTPException, Request

from latticeai.core.multi_agent import MultiAgentOrchestrator, default_role_runner
from latticeai.core.workflow_engine import WorkflowEngine


class PlatformRuntime:
    def __init__(
        self,
        *,
        store,
        workspace_service,
        plugin_registry,
        get_current_user: Callable[[Request], Optional[str]],
        workspace_graph: Callable[[], Any],
        workspace_scope_from_request: Callable[[Request], Optional[str]],
        get_tool_permission: Callable[..., Dict[str, Any]],
    ):
        self.store = store
        self.svc = workspace_service
        self.registry = plugin_registry
        self.get_current_user = get_current_user
        self.workspace_graph = workspace_graph
        self.scope_from_request = workspace_scope_from_request
        self.get_tool_permission = get_tool_permission

    # ── request gating ────────────────────────────────────────────────────

    def gate_read(self, request: Request) -> Optional[str]:
        try:
            return self.svc.resolve_read_scope(self.scope_from_request(request), self.get_current_user(request))
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    def gate_write(self, request: Request) -> Optional[str]:
        try:
            return self.svc.resolve_write_scope(self.scope_from_request(request), self.get_current_user(request))
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    def allowed_scopes(self, user: Optional[str]) -> Optional[Set[str]]:
        try:
            workspaces = self.svc.list_workspaces(user or None).get("workspaces", [])
            return {ws.get("workspace_id") for ws in workspaces if ws.get("workspace_id")}
        except Exception:
            return None

    # ── plugin lifecycle hooks ────────────────────────────────────────────

    def register_plugin_skill(self, skill_name: str, plugin_id: str):
        return self.store.mark_skill_installed(
            skill_name, version=f"plugin:{plugin_id}", metadata={"source": f"plugin:{plugin_id}"}
        )

    # ── shared node runners ───────────────────────────────────────────────

    def _tool_node_runner(self):
        """Workflow tool node: records the invocation + governance decision but
        never silently executes exec/destructive tools (those need approval)."""
        def runner(*, node, context):
            cfg = node.get("config") or {}
            name = cfg.get("tool") or ""
            try:
                permission = dict(self.get_tool_permission(name))
            except Exception:
                permission = {"tool": name, "risk": "unknown"}
            return {"tool": name, "args": cfg.get("args") or {}, "recorded": True, "permission": permission}
        return runner

    def _skill_node_runner(self):
        def runner(*, node, context):
            cfg = node.get("config") or {}
            name = cfg.get("skill") or ""
            entry = self.store.load_state().get("skill_registry", {}).get(name) or {}
            return {"skill": name, "found": bool(entry), "enabled": bool(entry.get("enabled"))}
        return runner

    def _context_provider(self, user, scope):
        def provider(goal: str):
            try:
                mems = self.store.search_memories(goal, user_email=user, workspace_id=scope).get("memories", [])
                return [str(m.get("content") or "")[:160] for m in mems[:5]]
            except Exception:
                return []
        return provider

    def plugin_capability_runners(self, user, scope) -> Dict[str, Callable[..., Any]]:
        """Runners the Plugin SDK boundary dispatches to (one per capability)."""
        def run_skill(*, plugin_id, action, args, manifest):
            return {"plugin": plugin_id, "ran_skills": manifest.provides.get("skills", [])}

        def run_tool(*, plugin_id, action, args, manifest):
            tool = args.get("tool") or (manifest.provides.get("tools") or [None])[0]
            try:
                permission = dict(self.get_tool_permission(tool)) if tool else {}
            except Exception:
                permission = {}
            return {"plugin": plugin_id, "tool": tool, "permission": permission, "recorded": True}

        def run_workflow(*, plugin_id, action, args, manifest):
            wf_id = args.get("workflow_id")
            if not wf_id:
                return {"plugin": plugin_id, "skipped": "no workflow_id"}
            return self.run_workflow_by_id(wf_id, user, scope, with_agent=False, inputs=args.get("inputs"))

        def run_agent(*, plugin_id, action, args, manifest):
            goal = args.get("goal") or f"Plugin {plugin_id} agent task"
            return self.run_agent(goal, user, scope, with_workflow=False, inputs=args.get("inputs"))

        return {"skills": run_skill, "tools": run_tool, "workflows": run_workflow, "agents": run_agent}

    def _plugin_node_runner(self, user, scope):
        def runner(*, node, context):
            cfg = node.get("config") or {}
            plugin_id = cfg.get("plugin_id") or cfg.get("plugin") or ""
            action = cfg.get("action") or "run_skill"
            result = self.registry.execute_action(
                plugin_id, action, cfg.get("args") or {}, runners=self.plugin_capability_runners(user, scope)
            )
            return result.as_dict()
        return runner

    def _agent_node_runner(self, user, scope):
        def runner(*, node, context):
            cfg = node.get("config") or {}
            goal = cfg.get("goal") or context.get("goal") or "Run agent"
            return self.run_agent(goal, user, scope, with_workflow=False, roles=cfg.get("roles"), inputs=context.get("inputs"))
        return runner

    # ── cross-system runs ─────────────────────────────────────────────────

    def run_workflow_by_id(self, workflow_id, user, scope, *, with_agent: bool, inputs=None) -> Dict[str, Any]:
        try:
            workflow = self.store.get_workflow(workflow_id, workspace_id=scope)
        except FileNotFoundError:
            return {"error": f"workflow not found: {workflow_id}"}
        runners = {
            "tool": self._tool_node_runner(),
            "skill": self._skill_node_runner(),
            "plugin": self._plugin_node_runner(user, scope),
        }
        if with_agent:
            runners["agent"] = self._agent_node_runner(user, scope)
        result = WorkflowEngine(runners).run(workflow, inputs=inputs or {})
        run = self.store.record_workflow_run(
            workflow_id=workflow_id, name=workflow.get("name") or "workflow",
            status=result.status, timeline=result.timeline, outputs=result.outputs,
            user_email=user, graph=self.workspace_graph(), workspace_id=scope,
        )
        return {"workflow_run_id": run["id"], "status": result.status}

    def run_agent(self, goal, user, scope, *, with_workflow: bool, roles=None, inputs=None) -> Dict[str, Any]:
        role_runner = default_role_runner(
            workflow_runner=(lambda wf_ref, ctx: self.run_workflow_by_id(wf_ref, user, scope, with_agent=False, inputs=ctx.inputs)) if with_workflow else None,
            plugin_runner=lambda pid, ctx: self.registry.execute_action(pid, "run_skill", {}, runners=self.plugin_capability_runners(user, scope)).as_dict(),
            context_provider=self._context_provider(user, scope),
        )
        result = MultiAgentOrchestrator(role_runner=role_runner).run(
            goal, user_email=user, workspace_id=scope, roles=roles, inputs=inputs or {}
        )
        run = self.store.record_agent_run(
            agent_id=result.agent_id, status=result.status, input_text=goal,
            output_text=result.output, timeline=result.timeline, relationships=[],
            user_email=user, graph=self.workspace_graph(), workspace_id=scope,
        )
        return {"agent_run_id": run["id"], "status": result.status, "output": result.output}

    # ── factories passed to routers ───────────────────────────────────────

    def build_workflow_runners(self, user, scope) -> Dict[str, Callable[..., Any]]:
        return {
            "tool": self._tool_node_runner(),
            "skill": self._skill_node_runner(),
            "plugin": self._plugin_node_runner(user, scope),
            "agent": self._agent_node_runner(user, scope),
        }

    def build_orchestrator(self, user, scope) -> MultiAgentOrchestrator:
        return MultiAgentOrchestrator(role_runner=default_role_runner(
            workflow_runner=lambda wf_ref, ctx: self.run_workflow_by_id(wf_ref, user, scope, with_agent=False, inputs=ctx.inputs),
            plugin_runner=lambda pid, ctx: self.registry.execute_action(pid, "run_skill", {}, runners=self.plugin_capability_runners(user, scope)).as_dict(),
            context_provider=self._context_provider(user, scope),
        ))
