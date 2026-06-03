# Lattice AI Workflow Designer

The Workflow Designer (introduced in **v2.0.0** and hardened in **v2.2.0**) lets
you build, validate, run, inspect, replay, export, and import automations as a
small **directed graph of typed nodes**. A workflow starts from a single
`trigger` node and walks node-to-node to an `output`, dispatching each executable
node to an injected *runner* that calls the real tool registry, skill registry,
plugin registry, or multi-agent orchestrator.

The execution model lives in
[`latticeai/core/workflow_engine.py`](../latticeai/core/workflow_engine.py)
(pure logic with injected runners) and the HTTP surface lives in
[`latticeai/api/workflow_designer.py`](../latticeai/api/workflow_designer.py).
Persistence reuses the existing `WorkspaceOSStore`, so pre-2.0 workflow history
is preserved.

The engine version is exported as:

```python
WORKFLOW_ENGINE_VERSION = "2.2.0"
```

## v2.2 hardening

- Agent node output is captured in workflow context and can flow into a later
  plugin or output node through `last_output`.
- Plugin node failures mark the run failed and emit realtime execution events.
- Workflow runs are replayable via `/workflows/api/runs/{run_id}/replay`, with
  frames for actor, time, reason, input, output, and decision.
- `record_workflow_run` emits `workflow_started`, `workflow_completed`, and
  `execution_failed` events over the existing SSE activity feed.

---

## Node types

A workflow is built from a fixed vocabulary of node types
(`workflow_engine.NODE_TYPES`):

```python
NODE_TYPES = (
    "trigger",
    "tool",
    "skill",
    "plugin",
    "agent",
    "condition",
    "output",
)
```

- `trigger` — structural entry point. Exactly one per workflow.
- `output` — structural exit point. Records the run output.
- `condition` — branches based on a safe comparator over the run context.
- `tool` / `skill` / `plugin` / `agent` — **executable** nodes, each dispatched
  to a runner of the matching family:

  ```python
  _RUNNER_FOR = {
      "tool": "tool",
      "skill": "skill",
      "plugin": "plugin",
      "agent": "agent",
  }
  ```

### Node shape

Every node is a JSON object with an `id`, a `type`, an optional `name`, a
`config` blob, and a `next` pointer to the id of the next node (`null`
terminates the branch):

```json
{
  "id": "n1",
  "type": "tool",
  "name": "Fetch report",
  "config": { "tool": "http_get", "args": { "url": "https://example.com" } },
  "next": "n2"
}
```

### Condition node shape

A `condition` node does **not** use `next`. Instead it defines `branches`,
mapping the evaluation result to the next node id. The standard branches are
`true` and `false` (either may be `null` to terminate that path):

```json
{
  "id": "check",
  "type": "condition",
  "name": "Has results?",
  "config": { "left": "count", "op": ">", "right": 0 },
  "branches": { "true": "notify", "false": "output" }
}
```

---

## Validation — `validate_definition`

```python
def validate_definition(workflow: Dict[str, Any]) -> List[str]: ...
```

Returns a list of error strings; an empty list (`[]`) means the workflow is
valid. The definition is normalized first (see below), then checked against
these rules:

1. **Non-empty** — a workflow with no nodes is invalid (`"workflow has no nodes"`).
2. **Unique ids** — duplicate node ids are rejected (`"duplicate node ids"`),
   and a node without an `id` is reported (`"node missing id"`).
3. **Exactly one trigger** — zero triggers reports
   `"workflow must have a trigger node"`; more than one reports
   `"workflow must have exactly one trigger node"`.
4. **Known types** — a node `type` outside `NODE_TYPES` reports
   `"node '<id>': unknown type '<type>'"`.
5. **Edges resolve** — every `next` (and every `condition` branch target) must
   either be `null` or point at a real node id, otherwise
   `"node '<id>' points at unknown node '<target>'"`.
6. **Conditions need branches** — a `condition` node missing a non-empty
   `branches` dict reports
   `"condition node '<id>' must define branches (e.g. true/false)"`.

---

## Normalization & backward compatibility — `normalize_definition`

```python
def normalize_definition(workflow: Dict[str, Any]) -> Dict[str, Any]: ...
```

`normalize_definition` returns a node-based definition and **never mutates its
input**.

- If the workflow already has a non-empty `nodes` list, it is returned as-is
  (with `id`, `name`, `nodes`, `metadata`).
- If it only has a legacy `steps` list (the pre-2.0 shape), it is **lifted into
  a linear `trigger -> tool... -> output` node chain**. Each step becomes a
  `tool` node whose `config` carries the original action and args, and a
  `"lifted_from_steps": true` marker is added to `metadata`.

> **Compatibility.** This is the mechanism that keeps **pre-2.0 workflow history
> working under v2.0**. A stored `{"steps": [...]}` workflow validates and runs
> unchanged — it is interpreted as a `trigger`-headed node chain at validate /
> run time, without rewriting persisted history. The change is purely additive:
> v1.x `steps` data is preserved, and `WorkspaceOSStore.create_workflow` stores
> the new typed `nodes` graph **alongside** the legacy `steps` list.

Lifted shape (illustrative):

```json
{
  "id": "trigger", "type": "trigger", "name": "Start",
  "config": { "trigger": "manual" }, "next": "step-0"
}
{
  "id": "step-0", "type": "tool", "name": "<action>",
  "config": { "tool": "<action>", "args": { } }, "next": "output"
}
{
  "id": "output", "type": "output", "name": "Output",
  "config": {}, "next": null
}
```

---

## Execution — `WorkflowEngine.run`

```python
class WorkflowEngine:
    def __init__(self, runners: Optional[Dict[str, Callable[..., Any]]] = None): ...

    def run(
        self,
        workflow: Dict[str, Any],
        *,
        inputs: Optional[Dict[str, Any]] = None,
    ) -> WorkflowRun: ...
```

`run` normalizes and validates the definition first. If validation fails, it
returns immediately with `status = "failed"` and a single `validation` timeline
entry carrying the error list.

Otherwise it walks the graph from the trigger node, building a step-by-step
`timeline`. A run context is seeded from `inputs`
(`{"inputs": inputs, **inputs}`), and each executable node's result is stored
under both `context["last_output"]` and `context[<node id>]` so later nodes and
conditions can read it.

### Node dispatch and per-node status

- **`trigger`** — recorded `ok`; advances to `next`.
- **`output`** — recorded `ok`; records its output (the node's
  `config.value`, or `context["last_output"]`) into `run.outputs[<id>]`.
- **`condition`** — recorded `ok`; evaluated safely (see below); follows the
  `true` or `false` branch.
- **`tool` / `skill` / `plugin` / `agent`** — dispatched to the runner for the
  node's family `runner(node=<node>, context=<context>)`:
  - **No runner configured** for that family → node recorded as `skipped`
    with a `reason` (`"no '<family>' runner configured"`); the run continues.
  - **Runner raises** → node recorded as `error` with the exception text in
    `reason`.
  - **Otherwise** → node recorded as `ok` with the runner's `result`.

### Overall run status

The aggregate `WorkflowRun.status` is derived from what happened:

- `"failed"` — any executable node errored (or the cycle guard tripped).
- `"partial"` — no errors, but at least one node was `skipped`.
- `"ok"` — every node completed successfully.

### Cycle guard

A mis-wired `next` cycle can never hang a run. The engine enforces a hard cap:

```python
_MAX_STEPS = 100  # hard cap so a mis-wired ``next`` cycle can never hang a run.
```

If the walk reaches `_MAX_STEPS`, a `guard` timeline entry with status `error`
is appended (`"exceeded 100 steps (cycle?)"`) and the run is marked `failed`.

### Safe condition evaluation — **no `eval`**

Conditions are evaluated by a comparator over the run context. **There is no
`eval` and no arbitrary code execution.** The config shape is:

```json
{ "left": "<context key>", "op": "==", "right": "<literal>" }
```

Supported `op` values: `==`, `!=`, `>`, `<`, `>=`, `<=`, `contains`, and
`truthy` (the default). `left` resolves from the run context by key (falling
back to `config.left_value`). Numeric comparisons coerce both sides with
`float()`. **Unknown keys or ops, and any evaluation error, fail closed onto the
`false` branch** — a misconfigured condition never crashes a run.

### `WorkflowRun`

```python
@dataclass
class WorkflowRun:
    workflow_id: Optional[str]
    name: str
    status: str = "ok"          # ok | failed | partial
    timeline: List[Dict[str, Any]] = field(default_factory=list)
    outputs: Dict[str, Any] = field(default_factory=dict)
    started_at: str = ...
    finished_at: Optional[str] = None
```

`WorkflowRun.as_dict()` adds a derived `step_count` (the timeline length). This
is the `result` object returned by the run endpoint.

---

## Runners and tool-node governance

In production, `server_app` injects a `build_runners` callable
(`PlatformRuntime.build_workflow_runners`) that returns one runner per family:

```python
{
    "tool":   <tool node runner>,
    "skill":  <skill node runner>,
    "plugin": <plugin node runner>,
    "agent":  <agent node runner>,
}
```

> **Safety: the tool node never silently executes destructive tools.** The
> `tool` runner records the invocation **and its governance decision** — it
> resolves the tool's permission record and returns
> `{"tool", "args", "recorded": true, "permission": {...}}` rather than
> executing the tool. Exec / destructive tools require approval and are **not**
> run implicitly from a workflow node. Skill / plugin / agent nodes dispatch to
> their respective registries (skill lookup, plugin action dispatch, multi-agent
> run) through the same injected map.

Because runners are injected, the engine is fully testable: tests pass fakes and
drive a complete `trigger → ... → output` run with no server, no LLM, and no
network.

---

## Run history & persistence

Runs are persisted through `WorkspaceOSStore.record_workflow_run`:

```python
def record_workflow_run(
    self,
    *,
    workflow_id: Optional[str],
    name: str,
    status: str,
    timeline: List[Dict[str, Any]],
    outputs: Optional[Dict[str, Any]] = None,
    user_email: Optional[str] = None,
    graph: Any = None,
    workspace_id: Optional[str] = None,
) -> Dict[str, Any]: ...
```

Each recorded run is **local-first** and:

- is **ingested into the Knowledge Graph** as a `WorkflowRun` event (the
  resulting `graph_node_id` is stored on the run; a `graph_error` is captured if
  ingest fails);
- emits a **Workspace timeline event** (`record_timeline_event("workflow",
  "workflow_run", ...)`);
- is **cross-linked** back onto the workflow's own event log (`{"type": "run",
  ...}`);
- is **capped** — `workflow_runs` retains the most recent **300** runs
  (`state["workflow_runs"][-300:]`).

Runs are workspace-scoped, so listings respect the read scope of the caller.

---

## HTTP API

All routes are namespaced under `/workflows` (to avoid colliding with
`/workspace/workflows`) and are created by `create_workflow_designer_router`.
Every route requires an authenticated user; mutating routes additionally pass
through the write gate (workspace scope), and reads through the read gate.

### Designer page

```
GET /workflows
```

Serves the Workflow Designer UI (`workflows.html`). Returns `404` if the UI
file / static dir is not available.

### Definitions

```
GET   /workflows/api/definitions          # list (optional ?q= search), read-scoped
POST  /workflows/api/definitions          # create (validated), write-scoped
GET   /workflows/api/definitions/{id}      # fetch one (404 if missing)
PATCH /workflows/api/definitions/{id}      # update name/nodes/metadata (validated if nodes given)
```

**Create / update request body** (`WorkflowDefinitionRequest` /
`WorkflowUpdateRequest`):

```json
{
  "name": "Daily digest",
  "nodes": [
    { "id": "trigger", "type": "trigger", "name": "Start",
      "config": { "trigger": "manual" }, "next": "out" },
    { "id": "out", "type": "output", "name": "Output",
      "config": {}, "next": null }
  ],
  "metadata": {}
}
```

On create/update the nodes are validated with `validate_definition`. Validation
failure returns `400` with:

```json
{ "detail": { "validation_errors": ["..."] } }
```

A successful create/update returns `{ "workflow": { ... } }`. Creating a
workflow also writes a legacy `steps` projection
(`[{"action": <type>, "node": <id>}]`) alongside the typed `nodes`, and emits a
`workflow_created` audit event.

### Validate

```
POST /workflows/api/validate
```

Request body (`WorkflowValidateRequest`, `name` defaults to `"Draft"`):

```json
{ "name": "Draft", "nodes": [ /* ... */ ] }
```

Response:

```json
{ "ok": true, "errors": [] }
```

### Run

```
POST /workflows/api/definitions/{id}/run
```

Request body (`WorkflowRunRequest`):

```json
{ "inputs": { "count": 3 } }
```

Loads the stored workflow (`404` if missing), builds the runner map for the
current user + scope, executes via `WorkflowEngine.run`, persists the run with
`record_workflow_run`, and emits a `workflow_run` audit event. Response:

```json
{
  "run":    { "id": "workflow-run-...", "status": "ok", "...": "..." },
  "result": {
    "workflow_id": "workflow-...",
    "name": "Daily digest",
    "status": "ok",
    "timeline": [ /* per-node entries */ ],
    "outputs": { "out": null },
    "started_at": "2026-06-01T09:00:00",
    "finished_at": "2026-06-01T09:00:00",
    "step_count": 2
  }
}
```

### Run history

```
GET /workflows/api/definitions/{id}/runs   # runs for one workflow (?limit=, default 50)
GET /workflows/api/runs                     # all runs in scope (?limit=, default 50)
```

Both are read-scoped and return `{ "runs": [ ... ] }` (most recent first;
`limit` is clamped to 1–300).

### Export / import (JSON round-trip)

```
GET  /workflows/api/export/{id}   # portable JSON (definition only)
POST /workflows/api/import        # create a workflow from exported JSON
```

`export_workflow` returns a portable, definition-only payload (no run history
or scope), stamped with the engine version and stripped of the internal
`lifted_from_steps` marker:

```json
{
  "lattice_workflow_export": "2.2.0",
  "name": "Daily digest",
  "nodes": [ /* ... */ ],
  "metadata": {}
}
```

Import request body (`WorkflowImportRequest`):

```json
{ "data": { "name": "Daily digest", "nodes": [ /* ... */ ], "metadata": {} } }
```

`import_workflow` validates the payload (raising `WorkflowError` → `400` on
invalid input), marks `metadata.imported = true`, persists a new workflow, and
emits a `workflow_imported` audit event. A successful import returns
`{ "workflow": { ... } }`.

> **Note.** Export carries the definition only — importing on another instance
> produces a fresh workflow id and a fresh, empty run history.

---

## Compatibility summary

- **Additive only.** v2.0 introduces the typed-node graph and the `/workflows`
  API surface without removing v1.x behavior or data.
- **Legacy data preserved.** Pre-2.0 `{"steps": [...]}` workflows still validate
  and run, lifted on the fly into a `trigger -> ... -> output` chain by
  `normalize_definition`. New workflows store typed `nodes` **alongside** the
  legacy `steps` projection.
- **Same persistence layer.** Workflows and runs are stored by the existing
  `WorkspaceOSStore`, remain workspace-scoped, and continue to feed the
  Knowledge Graph and Workspace timeline.
