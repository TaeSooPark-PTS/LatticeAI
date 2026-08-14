# Lattice AI Multi-Agent Runtime 2.1

The Multi-Agent Runtime is the **orchestration layer** introduced in v2.0.0 and
operationalized in v2.2.0. It sits
*above* the single-agent state machine, which since 11.6.0 is the Rust loop
orchestrator ([`lattice_agent::agentloop`](../rust/lattice-agent/src/agentloop/mod.rs))
and coordinates a pipeline of named **roles** that hand off work to one another,
retry on a failing review, and emit a fully observable, replayable timeline.

- **Source of truth:** `rust/lattice-platform/src/agents.rs` (the orchestration
  routes) over `rust/lattice-agent/src/agentloop/` (the state machine)
- **HTTP surface:** the `agents` family, mounted by `lattice-platform`
- **Persistence:** `rust/lattice-platform/src/review_queue.rs` — the same
  `workspace_os.json` document the Review Center reads

> **Moved in 11.6.0.** The Python modules this page used to name
> (`lattice_brain/runtime/multi_agent.py`, `latticeai/api/agents.py`,
> `latticeai/core/workspace_os.py`) were deleted when the product server became
> Rust. The behaviour described below is served natively.

```python
MULTI_AGENT_VERSION = "2.2.0"
```

## What v2.2 adds

v2.2 does not replace the v2.0 runtime; it makes the runtime's operational
objects durable and inspectable:

- **Explicit handoff records**: `handoff_id`, source/target agent ids, reason,
  task summary, context packet, status, and timestamps.
- **Agent context packets**: objective, task summary, workspace/graph/memory/
  workflow context, plugin outputs, constraints, reviewer notes, and retry
  metadata with obvious secret keys redacted.
- **Review / retry history**: reviewer outcomes normalize to `approve`,
  `reject`, or `retry`; retry reasons, notes, counts, and limits are persisted.
- **Planning records**: plans include a `plan_id`, ordered executable steps, and
  plan-review metadata.
- **Replay**: persisted runs can be replayed as frames showing actor, time,
  reason, input, output, and decision via `/agents/api/runs/{run_id}/replay`.

## How it relates to the v1 single-agent runtime

v1.x shipped a single-agent state machine — `SingleAgentRuntime` driving
`PLAN → EXECUTE → VERIFY → DONE` (with `ROLLBACK` / `FAILED` recovery paths) over an
injected `AgentDeps` port. The historical `AgentRuntime` import remains as a
compatibility alias.

v2.0 adds the *orchestration* layer on top: instead of one agent looping through
internal phases, the `MultiAgentOrchestrator` drives a **pipeline of distinct roles**
(researcher, planner, executor, reviewer, release) that hand off to one another and
can rewind on a failing review. The two layers are complementary — the v2
orchestrator coordinates roles; an individual role's runner could itself be backed by
the v1 `SingleAgentRuntime` loop or an LLM, but that is an implementation choice behind the
injected runner port.

> **Compatibility.** The Multi-Agent Runtime is purely additive. The v1
> `SingleAgentRuntime` state machine and its `/agent` endpoints are untouched, and the v2
> API is namespaced under `/agents` (plural) so it never collides with the existing
> single-agent `/agent` routes. Existing v1.x data is preserved; new runs are appended
> to workspace state and the Knowledge Graph alongside it.

## Built-in roles (`AGENT_ROLES`)

The runtime defines five built-in roles. Each role id matches an entry in
`latticeai.core.workspace_os.DEFAULT_AGENTS`, so orchestrated runs reference the same
agents that already appear in the Workspace.

```python
AGENT_ROLES = ("researcher", "planner", "executor", "reviewer", "release")

ROLE_AGENT_IDS = {
    "researcher": "agent:researcher",
    "planner":    "agent:planner",
    "executor":   "agent:executor",
    "reviewer":   "agent:reviewer",
    "release":    "agent:release",
}
```

| Role | Agent id | Responsibility |
| --- | --- | --- |
| `researcher` | `agent:researcher` | Gathers relevant context (workspace memory / graph) via an injected `context_provider`. |
| `planner` | `agent:planner` | Decomposes the goal into ordered, inspectable steps. |
| `executor` | `agent:executor` | Carries out steps; may drive an injected workflow / plugin runner. |
| `reviewer` | `agent:reviewer` | Judges the result and returns a `pass` / `retry` verdict. |
| `release` | `agent:release` | Finalizes / packages the outcome (optional). |

The `agent:*` ids correspond one-to-one with `DEFAULT_AGENTS` in `workspace_os`
(`agent:planner`, `agent:executor`, `agent:reviewer`, `agent:researcher`,
`agent:release`), which is why an orchestrated run can record relationships against
agents that the Workspace already knows about.

### The core pipeline

`researcher` and `release` are optional stages — they run only when explicitly
requested. A quick, default run is therefore three stages:

```python
CORE_PIPELINE = ("planner", "executor", "reviewer")
```

When `MultiAgentOrchestrator.run(...)` is called without an explicit `roles` list, it
uses `CORE_PIPELINE`. Any roles passed in are filtered to the set of known
`AGENT_ROLES`; if nothing valid remains, it falls back to `CORE_PIPELINE`.

## Orchestration: `MultiAgentOrchestrator.run`

```python
def run(
    self,
    goal: str,
    *,
    user_email: Optional[str] = None,
    workspace_id: Optional[str] = None,
    inputs: Optional[Dict[str, Any]] = None,
    roles: Optional[List[str]] = None,
    max_retries: int = 2,
) -> AgentRunResult:
    ...
```

`run` walks the resolved pipeline, threading a single `OrchestrationContext` through
every stage. As it goes it appends two kinds of events to an observable timeline:

- **`role` events** — emitted by `_run_role` for each stage, recording the role, its
  `agent_id`, status (`ok` / `error`), the raw runner result, and start/end timestamps.
- **`handoff` events** — emitted via `OrchestrationContext.handoff(frm, to, note)`
  whenever control passes from one role to the next (and on a retry rewind).

The timeline is also bracketed by a `start` event (carrying the goal and resolved
pipeline) and an `end` event (carrying the final status and retry count).

### Retry: reviewer rewinds to executor

The pipeline is ordinarily linear, but the **reviewer can rewind** the pipeline to the
executor. After the reviewer runs, if its verdict is `retry` and the retry budget is
not yet exhausted, the orchestrator:

1. increments `ctx.retries`,
2. emits a `handoff("reviewer", "executor", note="retry #N: <reason>")`,
3. resets the pipeline index back to the executor stage, and
4. re-runs executor → reviewer.

This repeats until the verdict is `pass` or `ctx.retries` reaches `max_retries`.

```text
planner → executor → reviewer ──pass──▶ (continue / end)
                        │
                        └──retry (and retries < max_retries)──▶ executor (rewind)
```

### Final status

The terminal status is derived from the final reviewer verdict and retry count:

| Condition | `status` |
| --- | --- |
| Final verdict `pass`, no retries | `ok` |
| Final verdict `pass`, after one or more retries | `retried_ok` |
| Final verdict not `pass` (retries exhausted) | `failed` |

### `OrchestrationContext`

The mutable carrier threaded through every stage:

```python
@dataclass
class OrchestrationContext:
    goal: str
    user_email: Optional[str] = None
    workspace_id: Optional[str] = None
    inputs: Dict[str, Any] = field(default_factory=dict)
    plan: List[Dict[str, Any]] = field(default_factory=list)
    research: List[str] = field(default_factory=list)
    executed: List[Dict[str, Any]] = field(default_factory=list)
    review: Dict[str, Any] = field(default_factory=dict)
    timeline: List[Dict[str, Any]] = field(default_factory=list)
    retries: int = 0
    output: str = ""

    def handoff(self, frm: str, to: str, note: str = "") -> None: ...
```

### `AgentRunResult`

`run` returns an `AgentRunResult`, which exposes `as_dict()` for serialization:

```python
@dataclass
class AgentRunResult:
    agent_id: str
    status: str  # ok | failed | retried_ok
    output: str
    timeline: List[Dict[str, Any]]
    plan: List[Dict[str, Any]]
    review: Dict[str, Any]
    roles_run: List[str]
    retries: int = 0
```

## The role runner is an injected port

Like the v1 runtime, the orchestrator is **pure logic over an injected `role_runner`
port**. It runs with no LLM and no server:

```python
class MultiAgentOrchestrator:
    def __init__(
        self,
        role_runner: Optional[Callable[[str, OrchestrationContext], Dict[str, Any]]] = None,
    ):
        self.role_runner = role_runner or default_role_runner()
```

The runner is a single callable `(role: str, ctx: OrchestrationContext) -> Dict[str, Any]`.
The orchestration logic — pipeline walking, handoffs, retry rewind, timeline emission,
status derivation — does not depend on *how* a role does its work. This means a
**production deployment can swap in an LLM-backed runner without touching the
orchestration layer**: implement the same callable signature, pass it to the
constructor, and the pipeline behaves identically while individual roles gain
model-backed reasoning.

If a runner raises, `_run_role` captures the exception into the `role` event as
`status: "error"` with an `{"error": ...}` result, rather than crashing the run.

## The default runner is deterministic and useful

`default_role_runner` builds a **dependency-free, deterministic** runner that
implements every built-in role with real (non-LLM) behavior. This is what makes
"agent runs can execute workflows / plugins" true in the community edition without
requiring a model.

```python
def default_role_runner(
    *,
    workflow_runner: Optional[Callable[..., Any]] = None,
    plugin_runner: Optional[Callable[..., Any]] = None,
    context_provider: Optional[Callable[[str], List[str]]] = None,
) -> Callable[[str, OrchestrationContext], Dict[str, Any]]:
    ...
```

Behavior by role:

- **`researcher`** — calls the injected `context_provider(goal)` (workspace memory) to
  pull relevant context into `ctx.research`; returns the count and the first items.
- **`planner`** — decomposes the goal into ordered steps. If `inputs["steps"]` is a
  non-empty list, each entry becomes a planned step; otherwise it produces a default
  three-step plan (`Analyze` / `Execute` / `Verify the result`). Steps are written to
  `ctx.plan`.
- **`executor`** — iterates the plan and marks each step `done`. A step may request a
  workflow or plugin run; when the corresponding runner is injected, the executor
  drives it (see below). Results are written to `ctx.executed` and a summary line to
  `ctx.output`.
- **`reviewer`** — passes only if `ctx.executed` is non-empty and *every* executed step
  has `status == "done"`; otherwise it returns `retry`. The verdict shape is:

  ```json
  {
    "verdict": "pass",
    "reason": "all steps completed",
    "confidence": 0.9
  }
  ```

  A failing review yields `{"verdict": "retry", "reason": "no steps executed", "confidence": 0.3}`.
- **`release`** — sets/keeps `ctx.output` and returns `{"released": true, "summary": ...}`.

An unrecognized role returns `{"role": role, "status": "noop"}`.

### Agent → workflow and agent → plugin integration

The executor role is where Lattice's cross-feature integration happens. When the
`default_role_runner` is built with a `workflow_runner` and/or `plugin_runner`, a plan
step can drive them:

- A step's `workflow` (or `inputs["workflow"]`) is run via `workflow_runner(wf, ctx)`
  on the first step (`index == 0`), capturing the result under `workflow_result` (or
  `workflow_error` on failure).
- A step's `plugin` is run via `plugin_runner(pl, ctx)`, capturing `plugin_result` (or
  `plugin_error`).

This is the **agent → workflow** and **agent → plugin** seam: an orchestrated agent run
can actually execute Workflows and Plugins, in the community edition, with no model
required.

## Persistence and Knowledge Graph

After a run completes, the API persists it via
`WorkspaceOSStore.record_agent_run`, which both ingests a Knowledge Graph node and
records a Workspace timeline event:

```python
def record_agent_run(
    self,
    *,
    agent_id: str,
    status: str,
    input_text: str,
    output_text: str,
    user_email: Optional[str],
    timeline: Optional[List[Dict[str, Any]]] = None,
    relationships: Optional[List[str]] = None,
    graph: Any = None,
    workspace_id: Optional[str] = None,
) -> Dict[str, Any]:
    ...
```

What it does:

- Builds a run record (`id`, `agent_id`, `status`, `input`, `output_preview` truncated
  to 1000 chars, `user_email`, scoped `workspace_id`, `relationships`, `timeline`,
  `created_at`).
- When a `graph` is supplied, calls `graph.ingest_event("AgentRun", ...)` and stores the
  returned `graph_node_id` on the run (capturing `graph_error` if ingest fails).
- Appends the run to workspace state (retaining the most recent 300) and records an
  `agent` / `agent_run` timeline event.

Runs are workspace-scoped; `list_agents(workspace_id=...)` returns the registered
`agents` plus the most recent runs for that scope.

## HTTP API

The router is created by `create_agents_router(...)` in `latticeai/api/agents.py`. All
paths live under `/agents` (plural).

> **Compatibility.** `/agents` does **not** collide with the existing single-agent
> `/agent` endpoints — the trailing `s` keeps the v2 namespace fully separate, so v1
> clients continue to work unchanged.

### `GET /agents` — UI page

Requires an authenticated user. Serves the `agents.html` Multi-Agent UI from the static
directory; returns `404` if the UI is not available or not found.

### `GET /agents/api/roles`

Requires an authenticated user. Lists the built-in roles and the default pipeline.

```json
{
  "roles": [
    {"role": "researcher", "agent_id": "agent:researcher"},
    {"role": "planner",    "agent_id": "agent:planner"},
    {"role": "executor",   "agent_id": "agent:executor"},
    {"role": "reviewer",   "agent_id": "agent:reviewer"},
    {"role": "release",    "agent_id": "agent:release"}
  ],
  "default_pipeline": ["planner", "executor", "reviewer"]
}
```

### `GET /agents/api/runs`

Requires an authenticated user; reads are scoped via `gate_read`. Returns the registered
agents plus recent runs for the resolved workspace scope (the result of
`store.list_agents(workspace_id=scope)`).

```json
{
  "agents": [ { "id": "agent:planner", "name": "Planner", "...": "..." } ],
  "runs":   [ { "id": "agent-run-...", "agent_id": "agent:executor", "status": "ok", "...": "..." } ]
}
```

### `POST /agents/api/run`

Requires an authenticated user; writes are scoped via `gate_write`. Runs the
orchestrator and persists the result.

**Request body** (`AgentRunRequest`):

```json
{
  "goal": "Summarize the open incidents and draft a status update",
  "roles": ["researcher", "planner", "executor", "reviewer"],
  "inputs": { "steps": ["Collect incidents", "Draft update"] },
  "max_retries": 2
}
```

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `goal` | string | — | Required; a `400` is returned if blank. |
| `roles` | string[] | `[]` | Empty means the default `CORE_PIPELINE`. Unknown roles are filtered out. |
| `inputs` | object | `{}` | Passed through to the runner (e.g. `steps`, `workflow`). |
| `max_retries` | int | `2` | Clamped server-side to the range `0`–`5`. |

The endpoint resolves an orchestrator via the injected `orchestrator_factory(user, scope)`,
runs it, records the run with `store.record_agent_run(...)` (passing the
`workspace_graph()` for KG ingest and the run's `roles_run` as `relationships`), and
appends a `multi_agent_run` audit event.

**Response:**

```json
{
  "run": {
    "id": "agent-run-…",
    "agent_id": "agent:executor",
    "status": "ok",
    "input": "Summarize the open incidents and draft a status update",
    "output_preview": "Completed 2 planned step(s) for: …",
    "relationships": ["agent:researcher", "agent:planner", "agent:executor", "agent:reviewer"],
    "timeline": [ { "event": "start", "...": "..." } ],
    "graph_node_id": "…",
    "created_at": "…"
  },
  "result": {
    "agent_id": "agent:executor",
    "status": "ok",
    "output": "Completed 2 planned step(s) for: …",
    "timeline": [ "…" ],
    "plan": [ { "index": 0, "description": "Collect incidents", "status": "done" } ],
    "review": { "verdict": "pass", "reason": "all steps completed", "confidence": 0.9 },
    "roles_run": ["researcher", "planner", "executor", "reviewer"],
    "retries": 0
  }
}
```

## Timeline event reference

The orchestration timeline is a flat, ordered list of event objects. Event types:

| `event` | Emitted by | Key fields |
| --- | --- | --- |
| `start` | `run` (before the pipeline) | `goal`, `pipeline`, `timestamp` |
| `role` | `_run_role` (per stage) | `role`, `agent_id`, `status`, `result`, `started_at`, `timestamp` |
| `handoff` | `OrchestrationContext.handoff` | `from`, `to`, `note`, `timestamp` |
| `end` | `run` (after the pipeline) | `status`, `retries`, `timestamp` |

This timeline is returned on the run result and persisted with the run, so it drops
straight into the Workspace timeline and Knowledge Graph.
