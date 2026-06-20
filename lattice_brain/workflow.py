"""Workflow engine — typed-node workflow definitions, validation, and a
deterministic execution interpreter with full run observability.

A workflow is a small directed graph of *nodes* starting from a ``trigger``.
Each node has a ``type`` (:data:`NODE_TYPES`), a ``config`` blob, and a ``next``
pointer (or a list of branches for ``condition`` nodes). The engine walks the
graph from the trigger, dispatching each node to an injected *runner* and
recording a step-by-step timeline so a run can be inspected, replayed, and
linked into the Workspace timeline / Knowledge Graph.

The engine is pure logic with injected runners, mirroring
:class:`latticeai.core.agent.AgentRuntime`:

* production wires runners that call the real tool registry, skill registry,
  plugin registry, and multi-agent orchestrator;
* tests pass fakes and drive a full trigger→...→output run with no server,
  no LLM, and no network.

Backward compatibility: legacy workflows persisted as a flat ``steps`` list
(pre-2.0) still validate and run — :func:`normalize_definition` lifts them into
a linear node chain so existing workflow history keeps working.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from lattice_brain.runtime.contracts import workflow_run_contract


WORKFLOW_ENGINE_VERSION = "2.2.0"

# The node vocabulary a workflow can be built from. ``trigger`` and ``output``
# are structural; the rest dispatch to an injected runner of the same family.
NODE_TYPES = (
    "trigger",
    "tool",
    "skill",
    "plugin",
    "agent",
    "condition",
    "output",
)

# Which runner family handles each executable node type.
_RUNNER_FOR = {
    "tool": "tool",
    "skill": "skill",
    "plugin": "plugin",
    "agent": "agent",
}

_MAX_STEPS = 100  # hard cap so a mis-wired ``next`` cycle can never hang a run.


class WorkflowError(Exception):
    """Raised for invalid workflow definitions."""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def normalize_definition(workflow: Dict[str, Any]) -> Dict[str, Any]:
    """Return a node-based definition, lifting legacy ``steps`` lists if needed.

    Never mutates the input. A legacy ``{"steps": [...]}`` workflow becomes a
    linear ``trigger -> tool... -> output`` node chain so it validates and runs
    under the v2.0 engine without rewriting stored history.
    """
    nodes = workflow.get("nodes")
    if isinstance(nodes, list) and nodes:
        return {
            "id": workflow.get("id"),
            "name": workflow.get("name") or "Untitled workflow",
            "nodes": nodes,
            "metadata": workflow.get("metadata") or {},
        }

    steps = workflow.get("steps") or []
    lifted: List[Dict[str, Any]] = [{
        "id": "trigger",
        "type": "trigger",
        "name": "Start",
        "config": {"trigger": "manual"},
        "next": "step-0" if steps else "output",
    }]
    for index, step in enumerate(steps):
        action = str(step.get("action") or "tool") if isinstance(step, dict) else "tool"
        nxt = f"step-{index + 1}" if index + 1 < len(steps) else "output"
        lifted.append({
            "id": f"step-{index}",
            "type": "tool",
            "name": action,
            "config": {"tool": action, "args": step if isinstance(step, dict) else {"value": step}},
            "next": nxt,
        })
    lifted.append({"id": "output", "type": "output", "name": "Output", "config": {}, "next": None})
    return {
        "id": workflow.get("id"),
        "name": workflow.get("name") or "Untitled workflow",
        "nodes": lifted,
        "metadata": {**(workflow.get("metadata") or {}), "lifted_from_steps": True},
    }


def validate_definition(workflow: Dict[str, Any]) -> List[str]:
    """Return a list of validation errors ([] means valid)."""
    errors: List[str] = []
    definition = normalize_definition(workflow)
    nodes = definition["nodes"]
    if not isinstance(nodes, list) or not nodes:
        return ["workflow has no nodes"]

    ids = [node.get("id") for node in nodes]
    if len(set(ids)) != len(ids):
        errors.append("duplicate node ids")
    id_set = {nid for nid in ids if nid}

    triggers = [node for node in nodes if node.get("type") == "trigger"]
    if not triggers:
        errors.append("workflow must have a trigger node")
    elif len(triggers) > 1:
        errors.append("workflow must have exactly one trigger node")

    for node in nodes:
        nid = node.get("id")
        ntype = node.get("type")
        if not nid:
            errors.append("node missing id")
        if ntype not in NODE_TYPES:
            errors.append(f"node '{nid}': unknown type '{ntype}'")
        # Validate edges point at real nodes (None terminates a branch).
        targets: List[Any] = []
        if ntype == "condition":
            branches = node.get("branches") or {}
            if not isinstance(branches, dict) or not branches:
                errors.append(f"condition node '{nid}' must define branches (e.g. true/false)")
            else:
                targets.extend(branches.values())
        else:
            targets.append(node.get("next"))
        for target in targets:
            if target is not None and target not in id_set:
                errors.append(f"node '{nid}' points at unknown node '{target}'")
    return errors


def _entry_node(nodes: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for node in nodes:
        if node.get("type") == "trigger":
            return node
    return nodes[0] if nodes else None


def _evaluate_condition(config: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Safe condition evaluation — NO eval. Compares a context value to a literal.

    config: ``{"left": "<context key>", "op": "==|!=|>|<|>=|<=|contains|truthy",
    "right": <literal>}``. Unknown keys / ops resolve to ``False`` so a
    mis-configured condition fails closed onto the ``false`` branch.
    """
    left_key = config.get("left")
    op = str(config.get("op") or "truthy")
    right = config.get("right")
    left = context.get(left_key) if left_key in context else config.get("left_value")
    try:
        if op == "truthy":
            return bool(left)
        if op == "==":
            return left == right
        if op == "!=":
            return left != right
        if op == "contains":
            return right in left  # type: ignore[operator]
        if op in (">", "<", ">=", "<="):
            lf, rf = float(left), float(right)  # type: ignore[arg-type]
            return {">": lf > rf, "<": lf < rf, ">=": lf >= rf, "<=": lf <= rf}[op]
    except Exception:
        return False
    return False


@dataclass
class WorkflowRun:
    workflow_id: Optional[str]
    name: str
    status: str = "ok"  # ok | failed | partial | awaiting_approval
    timeline: List[Dict[str, Any]] = field(default_factory=list)
    outputs: Dict[str, Any] = field(default_factory=dict)
    started_at: str = field(default_factory=_now)
    finished_at: Optional[str] = None
    # Suspension cursor (status == awaiting_approval): the paused node, what
    # it is waiting for, and a JSON-serializable context snapshot resume()
    # re-enters with — completed nodes are never re-executed.
    paused_node: Optional[str] = None
    pending_approval: Optional[Dict[str, Any]] = None
    paused_context: Optional[Dict[str, Any]] = None

    def as_dict(self) -> Dict[str, Any]:
        # Native workflow-run shape is preserved unchanged for existing readers.
        # A normalized ``agent-run-contract/v1`` projection is attached under
        # ``contract`` so workflow runs sit in the same observability family as
        # agent runs, audit events and realtime events (mirrors the audit-log
        # and realtime-bus convention). The projection is built from the native
        # dict — never from ``self`` — so it cannot recurse back into as_dict().
        body = {
            "workflow_id": self.workflow_id,
            "name": self.name,
            "status": self.status,
            "timeline": self.timeline,
            "outputs": self.outputs,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "step_count": len(self.timeline),
            "paused_node": self.paused_node,
            "pending_approval": self.pending_approval,
            "paused_context": self.paused_context,
        }
        body["contract"] = workflow_run_contract(body)
        return body


class ApprovalRequired(Exception):
    """A node needs an explicit human decision before it may execute.

    Raised by governed runners (e.g. a non-auto-approve tool). The engine
    pauses the run into ``awaiting_approval`` with a serializable cursor —
    it never records a fake success and never silently skips the node.
    """

    def __init__(self, message: str, *, tool: Optional[str] = None,
                 args: Optional[Dict[str, Any]] = None,
                 permission: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.tool = tool
        self.args = args or {}
        self.permission = permission or {}


def _json_safe(value: Any) -> Any:
    """Round-trip through JSON so paused context is durably serializable."""
    import json as _json

    return _json.loads(_json.dumps(value, ensure_ascii=False, default=str))


class WorkflowEngine:
    """Interprets a validated workflow definition over injected runners.

    ``runners`` maps a family ("tool" / "skill" / "plugin" / "agent") to a
    callable ``runner(node, context) -> Any``. A missing runner records the
    node as ``skipped`` with a reason rather than failing the whole run, so a
    workflow that references a capability the host has not wired degrades
    gracefully (and the gap is visible in the timeline).

    Suspension model (v4): a runner raising :class:`ApprovalRequired` pauses
    the run (status ``awaiting_approval``) with the node cursor and a
    JSON-serializable context snapshot. :meth:`resume` re-enters at the
    paused node — completed nodes are NEVER re-executed.
    """

    def __init__(self, runners: Optional[Dict[str, Callable[..., Any]]] = None, *, hooks: Any = None):
        self.runners = runners or {}
        # Optional lifecycle hooks registry. When present, ``run`` fires the
        # ``workflow`` hooks at workflow start and end so automation registered
        # against the workflow lifecycle actually executes.
        self.hooks = hooks

    def run(self, workflow: Dict[str, Any], *, inputs: Optional[Dict[str, Any]] = None) -> WorkflowRun:
        definition = normalize_definition(workflow)
        errors = validate_definition(definition)
        run = WorkflowRun(workflow_id=definition.get("id"), name=definition.get("name") or "workflow")
        if self.hooks is not None:
            self.hooks.fire_hook(
                "pre_workflow", "workflow.start",
                payload={"workflow_id": definition.get("id"), "name": definition.get("name"), "valid": not errors},
            )
        if errors:
            run.status = "failed"
            run.timeline.append({"node": None, "type": "validation", "status": "failed", "errors": errors, "timestamp": _now()})
            run.finished_at = _now()
            if self.hooks is not None:
                self.hooks.fire_hook(
                    "post_workflow", "workflow.end",
                    payload={"workflow_id": definition.get("id"), "status": run.status},
                )
            return run

        nodes = {node["id"]: node for node in definition["nodes"]}
        context: Dict[str, Any] = {"inputs": inputs or {}, **(inputs or {})}
        current = _entry_node(definition["nodes"])
        return self._execute(definition, run, nodes, context, current)

    def resume(
        self,
        workflow: Dict[str, Any],
        *,
        paused_node: str,
        paused_context: Dict[str, Any],
        approved: bool,
        prior_timeline: Optional[List[Dict[str, Any]]] = None,
    ) -> WorkflowRun:
        """Re-enter a paused run at its cursor; completed nodes never re-run.

        ``approved=True`` marks the paused node as human-approved (its runner
        sees the node id in ``context['__approved_nodes__']``); ``False``
        records an explicit denial and fails the run honestly.
        """
        definition = normalize_definition(workflow)
        nodes = {node["id"]: node for node in definition["nodes"]}
        node = nodes.get(paused_node)
        run = WorkflowRun(workflow_id=definition.get("id"), name=definition.get("name") or "workflow")
        if prior_timeline:
            run.timeline.extend(prior_timeline)
        if node is None:
            run.status = "failed"
            run.timeline.append({"node": paused_node, "type": "resume", "status": "failed",
                                 "reason": "paused node no longer exists in the definition",
                                 "timestamp": _now()})
            run.finished_at = _now()
            return run
        context: Dict[str, Any] = dict(paused_context or {})
        if not approved:
            run.status = "failed"
            run.timeline.append({"node": paused_node, "type": node.get("type"),
                                 "name": node.get("name") or paused_node,
                                 "status": "denied",
                                 "reason": "approval denied by the user",
                                 "timestamp": _now()})
            run.finished_at = _now()
            return run
        approvals = set(context.get("__approved_nodes__") or [])
        approvals.add(paused_node)
        context["__approved_nodes__"] = sorted(approvals)
        return self._execute(definition, run, nodes, context, node)

    def _execute(
        self,
        definition: Dict[str, Any],
        run: WorkflowRun,
        nodes: Dict[str, Dict[str, Any]],
        context: Dict[str, Any],
        current: Optional[Dict[str, Any]],
    ) -> WorkflowRun:
        steps = 0
        had_error = False
        had_skip = False
        while current is not None and steps < _MAX_STEPS:
            steps += 1
            ntype = current.get("type")
            nid = current.get("id")
            entry: Dict[str, Any] = {
                "node": nid,
                "type": ntype,
                "name": current.get("name") or nid,
                "timestamp": _now(),
            }

            if ntype == "trigger":
                entry["status"] = "ok"
                entry["trigger"] = (current.get("config") or {}).get("trigger", "manual")
                run.timeline.append(entry)
                current = nodes.get(current.get("next")) if current.get("next") else None
                continue

            if ntype == "output":
                entry["status"] = "ok"
                payload = (current.get("config") or {}).get("value")
                entry["output"] = payload if payload is not None else context.get("last_output")
                run.outputs[nid] = entry["output"]
                run.timeline.append(entry)
                current = nodes.get(current.get("next")) if current.get("next") else None
                continue

            if ntype == "condition":
                result = _evaluate_condition(current.get("config") or {}, context)
                entry["status"] = "ok"
                entry["result"] = result
                run.timeline.append(entry)
                branches = current.get("branches") or {}
                target = branches.get("true" if result else "false")
                current = nodes.get(target) if target else None
                continue

            # Executable node → dispatch to its runner family.
            family = _RUNNER_FOR.get(ntype)
            runner = self.runners.get(family) if family else None
            if runner is None:
                entry["status"] = "skipped"
                entry["reason"] = f"no '{family}' runner configured"
                had_skip = True
                run.timeline.append(entry)
                current = nodes.get(current.get("next")) if current.get("next") else None
                continue
            try:
                result = runner(node=current, context=context)
                entry["status"] = "ok"
                entry["result"] = result
                context["last_output"] = result
                context[nid] = result
            except ApprovalRequired as pause:
                # Suspend — never a fake success, never a silent skip.
                entry["status"] = "awaiting_approval"
                entry["pending"] = {
                    "tool": pause.tool, "args": pause.args,
                    "permission": pause.permission, "reason": str(pause),
                }
                run.timeline.append(entry)
                run.status = "awaiting_approval"
                run.paused_node = nid
                run.pending_approval = entry["pending"]
                try:
                    run.paused_context = _json_safe(context)
                except Exception:
                    run.paused_context = {"inputs": context.get("inputs") or {}}
                if self.hooks is not None:
                    self.hooks.fire_hook(
                        "post_workflow", "workflow.paused",
                        payload={"workflow_id": definition.get("id"),
                                 "status": run.status, "node": nid},
                    )
                return run
            except Exception as exc:
                entry["status"] = "error"
                entry["reason"] = str(exc)
                had_error = True
            run.timeline.append(entry)
            current = nodes.get(current.get("next")) if current.get("next") else None

        if steps >= _MAX_STEPS:
            run.timeline.append({"node": None, "type": "guard", "status": "error", "reason": f"exceeded {_MAX_STEPS} steps (cycle?)", "timestamp": _now()})
            had_error = True

        run.status = "failed" if had_error else ("partial" if had_skip else "ok")
        run.finished_at = _now()
        if self.hooks is not None:
            self.hooks.fire_hook(
                "post_workflow", "workflow.end",
                payload={"workflow_id": definition.get("id"), "name": definition.get("name"),
                         "status": run.status, "steps": steps},
            )
        return run


def export_workflow(workflow: Dict[str, Any]) -> Dict[str, Any]:
    """Portable JSON representation (definition only — no run history / scope)."""
    definition = normalize_definition(workflow)
    return {
        "lattice_workflow_export": WORKFLOW_ENGINE_VERSION,
        "name": definition.get("name"),
        "nodes": definition.get("nodes"),
        "metadata": {k: v for k, v in (definition.get("metadata") or {}).items() if k != "lifted_from_steps"},
    }


def import_workflow(data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate an exported workflow and return a definition ready to persist."""
    if not isinstance(data, dict):
        raise WorkflowError("import payload must be a JSON object")
    definition = {
        "name": data.get("name") or "Imported workflow",
        "nodes": data.get("nodes") or [],
        "metadata": {**(data.get("metadata") or {}), "imported": True},
    }
    errors = validate_definition(definition)
    if errors:
        raise WorkflowError("; ".join(errors))
    return definition
