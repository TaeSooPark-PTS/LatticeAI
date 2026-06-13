"""v2 Agentic Workspace Platform runtime — cross-system wiring.

This is the single place the v2 subsystems (Plugin SDK, Workflow
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

from lattice_brain.runtime.hooks import dispatch_tool
from lattice_brain.runtime.multi_agent import MultiAgentOrchestrator, default_role_runner, llm_role_runner
from lattice_brain.workflow import ApprovalRequired, WorkflowEngine
from tools import execute_tool


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
        hooks: Any = None,
        llm_generate: Optional[Callable[..., str]] = None,
        llm_available: Optional[Callable[[], bool]] = None,
        agent_registry: Any = None,
    ):
        self.store = store
        self.svc = workspace_service
        self.registry = plugin_registry
        self.get_current_user = get_current_user
        self.workspace_graph = workspace_graph
        self.scope_from_request = workspace_scope_from_request
        self.get_tool_permission = get_tool_permission
        # Lifecycle hooks registry — wires the workflow runtime + workflow tool
        # nodes into the same pre_*/post_* lifecycle as the HTTP + agent paths.
        self.hooks = hooks
        # v4 (T7b): a synchronous model bridge. When a model is loaded,
        # build_orchestrator returns the REAL (mode='llm') runner; otherwise
        # the deterministic runner, honestly labeled mode='simulation'.
        self.llm_generate = llm_generate
        self.llm_available = llm_available or (lambda: False)
        self.agent_registry = agent_registry

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
        """Workflow tool node: EXECUTES the tool under governance (v4).

        Auto-approve tools run immediately through the shared dispatch_tool
        lifecycle. Tools whose policy requires approval raise
        :class:`ApprovalRequired` so the engine pauses the run into
        ``awaiting_approval`` — never a silent ``{recorded: true}`` success,
        never an unapproved execution. A resumed run carries the approved
        node id in ``context['__approved_nodes__']``.
        """
        def runner(*, node, context):
            cfg = node.get("config") or {}
            name = cfg.get("tool") or ""
            args = cfg.get("args") or {}
            if not name:
                raise ValueError("tool node has no tool configured")
            try:
                permission = dict(self.get_tool_permission(name, args))
            except TypeError:
                permission = dict(self.get_tool_permission(name))
            approved_nodes = set(context.get("__approved_nodes__") or [])
            if permission.get("requires_approval") and node.get("id") not in approved_nodes:
                raise ApprovalRequired(
                    f"tool '{name}' requires explicit approval before a workflow may run it",
                    tool=name, args=args, permission=permission,
                )

            def _execute():
                return execute_tool(name, args)

            # Same tool lifecycle as the HTTP + agent paths (a pre_tool block
            # raises PermissionError, surfaced as the node error by the engine).
            result = dispatch_tool(self.hooks, name, args, _execute, source="workflow")
            return {"tool": name, "args": args, "executed": True,
                    "permission": permission, "result": result}
        return runner

    def _skill_node_runner(self):
        """Skill nodes refuse honestly: a skill is an instruction package for
        an LLM; without a model-driven executor there is nothing to run, and
        pretending otherwise (the pre-v4 existence check that reported 'ok')
        is exactly the fake functionality v4 bans."""
        def runner(*, node, context):
            cfg = node.get("config") or {}
            name = cfg.get("skill") or ""
            entry = self.store.load_state().get("skill_registry", {}).get(name) or {}
            if not entry:
                raise ValueError(f"skill '{name}' is not installed")
            raise RuntimeError(
                f"skill '{name}' requires LLM-driven execution, which workflow "
                "skill nodes do not provide in this build — refusing to fake a result"
            )
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
            raise RuntimeError(
                f"plugin '{plugin_id}' skill execution requires an LLM-driven "
                "runner, which this build does not provide — refusing to fake a result"
            )

        def run_tool(*, plugin_id, action, args, manifest):
            tool = args.get("tool") or (manifest.provides.get("tools") or [None])[0]
            if not tool:
                raise ValueError(f"plugin '{plugin_id}' run_tool needs a tool name")
            permission = dict(self.get_tool_permission(tool))
            if permission.get("requires_approval"):
                raise ApprovalRequired(
                    f"plugin tool '{tool}' requires explicit approval",
                    tool=tool, args=args, permission=permission,
                )
            result = dispatch_tool(self.hooks, tool, args, lambda: execute_tool(tool, args),
                                   source=f"plugin:{plugin_id}")
            return {"plugin": plugin_id, "tool": tool, "permission": permission,
                    "executed": True, "result": result}

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
                plugin_id, action, cfg.get("args") or {}, runners=self.plugin_capability_runners(user, scope), workspace_id=scope
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
        result = WorkflowEngine(runners, hooks=self.hooks).run(workflow, inputs=inputs or {})
        run = self.store.record_workflow_run(
            workflow_id=workflow_id, name=workflow.get("name") or "workflow",
            status=result.status, timeline=result.timeline, outputs=result.outputs,
            user_email=user, graph=self.workspace_graph(), workspace_id=scope,
            mode="live",
            pause={"node": result.paused_node, "pending": result.pending_approval,
                   "context": result.paused_context} if result.status == "awaiting_approval" else None,
        )
        return {"workflow_run_id": run["id"], "status": result.status}

    def run_agent(self, goal, user, scope, *, with_workflow: bool, roles=None, inputs=None) -> Dict[str, Any]:
        role_runner = default_role_runner(
            workflow_runner=(lambda wf_ref, ctx: self.run_workflow_by_id(wf_ref, user, scope, with_agent=False, inputs=ctx.inputs)) if with_workflow else None,
            plugin_runner=lambda pid, ctx: self.registry.execute_action(pid, "run_skill", {}, runners=self.plugin_capability_runners(user, scope), workspace_id=scope).as_dict(),
            context_provider=self._context_provider(user, scope),
        )
        result = MultiAgentOrchestrator(role_runner=role_runner).run(
            goal, user_email=user, workspace_id=scope, roles=roles, inputs=inputs or {}
        )
        run = self.store.record_agent_run(
            agent_id=result.agent_id, status=result.status, input_text=goal,
            output_text=result.output, timeline=result.timeline, relationships=[],
            handoffs=result.handoffs, context_packets=result.context_packets,
            plan=result.plan, plan_review=result.plan_review,
            review_history=result.review_history, retry_history=result.retry_history,
            memory_snapshots=result.memory_snapshots,
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
        workflow_runner = lambda wf_ref, ctx: self.run_workflow_by_id(wf_ref, user, scope, with_agent=False, inputs=ctx.inputs)  # noqa: E731
        plugin_runner = lambda pid, ctx: self.registry.execute_action(pid, "run_skill", {}, runners=self.plugin_capability_runners(user, scope), workspace_id=scope).as_dict()  # noqa: E731
        context_provider = self._context_provider(user, scope)
        custom_agents = {}
        if self.agent_registry is not None:
            try:
                custom_agents = {
                    a["id"]: a for a in self.agent_registry.all()
                    if str(a.get("id", "")).startswith("agent:custom:") and a.get("enabled", True)
                }
            except Exception:
                custom_agents = {}
        if self.llm_generate is not None and self.llm_available():
            from latticeai.core.agent_prompts import CRITIC_PROMPT, PLANNER_PROMPT

            return MultiAgentOrchestrator(
                role_runner=llm_role_runner(
                    generate=self.llm_generate,
                    planner_prompt=PLANNER_PROMPT,
                    critic_prompt=CRITIC_PROMPT,
                    context_provider=context_provider,
                    workflow_runner=workflow_runner,
                    plugin_runner=plugin_runner,
                    custom_agents=custom_agents,
                ),
                mode="llm",
                custom_agents=custom_agents,
            )
        return MultiAgentOrchestrator(role_runner=default_role_runner(
            workflow_runner=workflow_runner,
            plugin_runner=plugin_runner,
            context_provider=context_provider,
        ), mode="simulation", custom_agents=custom_agents)
