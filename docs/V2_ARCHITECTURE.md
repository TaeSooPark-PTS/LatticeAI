# Lattice AI v2.0 Architecture — Agentic Workspace Platform

Lattice AI v2.0.0 turns the local-first Workspace OS into a full **Agentic
Workspace Platform**: a single FastAPI application in which plugins, designed
workflows, multi-agent runs, and a realtime collaboration feed all compose over
the same local-first JSON store and Knowledge Graph.

This document describes how the four v2.0 pillars fit together, the small set of
**additive integration seams** that wire them, the cross-integration matrix that
results, and the compatibility surfaces that v1.x callers and data keep relying
on. Every claim below is grounded in the shipping source:

- App assembly: `latticeai/server_app.py`
- Cross-system wiring: `latticeai/services/platform_runtime.py`
- State + persistence: `latticeai/core/workspace_os.py`
- Plugin SDK: `latticeai/core/plugins.py`, `latticeai/api/plugins.py`
- Workflow engine: `latticeai/core/workflow_engine.py`, `latticeai/api/workflow_designer.py`
- Multi-Agent runtime: `latticeai/core/multi_agent.py`, `latticeai/api/agents.py`
- Realtime bus: `latticeai/core/realtime.py`, `latticeai/api/realtime.py`
- Project conventions: `AGENTS.md`

All four subsystems share the same design rules from `AGENTS.md`: dependency
injection, explicit interfaces, small focused modules, registry-based dispatch,
and composition over global state. None of them import the FastAPI app; each is
constructed by `server_app.py` and exposed through a router factory.

---

## 1. The Four v2.0 Pillars

The platform version is the single source of truth `WORKSPACE_OS_VERSION =
"2.0.0"` (`latticeai/core/workspace_os.py`). Each pillar module re-declares the
same version for its own surface (`PLUGIN_SDK_VERSION`, `WORKFLOW_ENGINE_VERSION`,
`MULTI_AGENT_VERSION`, `REALTIME_VERSION`).

### 1.1 Plugin SDK (`latticeai.core.plugins`)

A plugin is a directory under the configured plugins root that ships a
`plugin.json` manifest and **extends** the existing Skill / Tool / Workflow
surfaces rather than replacing them. Installed standalone skills keep working
untouched; a plugin simply *bundles* skills and declares tools / workflow
templates / actions under one versioned, permissioned unit.

Design rules enforced by the module: no import-time I/O (the filesystem is only
touched on `discover()`), no FastAPI and no globals (lifecycle state lives in the
Workspace OS store), and permissions are an allow-list — the execution boundary
refuses any capability a plugin did not declare *and* was not granted.

A validated `plugin.json` manifest:

```json
{
  "id": "release-notes",
  "name": "Release Notes Assistant",
  "version": "1.0.0",
  "description": "Bundles a release-notes skill and a packaging workflow.",
  "author": "you@example.com",
  "lattice_version": ">=2.0.0",
  "permissions": ["read_workspace", "run_skills", "run_workflows"],
  "provides": {
    "skills": ["release-notes"],
    "workflows": ["package-and-tag"],
    "tools": [],
    "actions": []
  },
  "entrypoint": "",
  "homepage": ""
}
```

Constraints from `validate_manifest` and the module constants:

- `id`: lowercase alphanumeric with `-`/`_`, 2–64 chars (`_ID_RE`).
- `version`: semantic version (`_SEMVER_RE`).
- `permissions` ⊆ `PLUGIN_PERMISSIONS` = `read_workspace`, `write_workspace`,
  `read_graph`, `write_graph`, `run_tools`, `run_skills`, `run_workflows`,
  `run_agents`, `network`, `manage_memory`.
- `provides` keys ⊆ `PLUGIN_PROVIDES` = `skills`, `tools`, `workflows`,
  `actions`; each value is a list.
- `lattice_version` is a minimum (major must match host, host must be `>=`),
  checked by `is_compatible(required, current=PLUGIN_SDK_VERSION)`.

The permissioned execution boundary:

```python
def execute_action(
    self,
    plugin_id: str,
    action: str,
    args: Optional[Dict[str, Any]] = None,
    *,
    runners: Optional[Dict[str, Callable[..., Any]]] = None,
) -> PluginExecutionResult: ...
```

`execute_action` maps an action to the capability + permission it needs
(`run_tool→tools/run_tools`, `run_skill→skills/run_skills`,
`run_workflow→workflows/run_workflows`, `run_agent→agents/run_agents`). It
returns a `PluginExecutionResult` with `status` in `ok | blocked | error |
skipped`:

- `blocked` if the plugin is not enabled, did not declare the required
  permission, or the permission was not granted at install time.
- `skipped` if the host injected no runner for the capability (never crashes the
  caller — safe-by-default).
- `error` if the runner raised; `ok` otherwise.

### 1.2 Workflow Designer (`latticeai.core.workflow_engine`)

A workflow is a small directed graph of typed *nodes* starting from a `trigger`.
`NODE_TYPES` = `trigger`, `tool`, `skill`, `plugin`, `agent`, `condition`,
`output`. The engine walks the graph from the trigger, dispatching each
executable node to an injected runner and recording a step-by-step timeline so a
run can be inspected, replayed, and linked into the Workspace timeline and
Knowledge Graph.

Key safety properties baked into the engine:

- **Bounded execution.** `_MAX_STEPS = 100` is a hard cap, so a mis-wired `next`
  cycle can never hang a run; exceeding it records a `guard` error.
- **Graceful degradation.** A node whose runner family is not wired is recorded
  as `skipped` with a reason rather than failing the whole run; the run status
  becomes `partial`.
- **No `eval`.** `condition` nodes use `_evaluate_condition`, which compares a
  context value to a literal via a fixed op set (`==`, `!=`, `>`, `<`, `>=`,
  `<=`, `contains`, `truthy`) and fails closed onto the `false` branch.

```python
class WorkflowEngine:
    def __init__(self, runners: Optional[Dict[str, Callable[..., Any]]] = None): ...
    def run(self, workflow: Dict[str, Any], *, inputs: Optional[Dict[str, Any]] = None) -> WorkflowRun: ...
```

A run yields a `WorkflowRun` with `status` in `ok | failed | partial`, a
`timeline`, and `outputs`. `export_workflow` / `import_workflow` provide a
portable JSON representation (definition only — no run history or scope).

### 1.3 Multi-Agent Runtime 2.0 (`latticeai.core.multi_agent`)

v1.x shipped a single-agent state machine (`latticeai.core.agent.AgentRuntime`:
PLAN → EXECUTE → VERIFY → DONE). v2.0 adds the **orchestration** layer above it:
a pipeline of named roles that hand off to one another, retry on a failing
review, and emit a structured timeline that drops straight into the Workspace
timeline / Knowledge Graph.

`AGENT_ROLES` = `researcher`, `planner`, `executor`, `reviewer`, `release`. The
`CORE_PIPELINE` is `planner → executor → reviewer`; `researcher` and `release`
are optional stages. Role ids match `DEFAULT_AGENTS` in
`latticeai/core/workspace_os.py` (`agent:planner`, `agent:executor`,
`agent:reviewer`, `agent:researcher`, `agent:release`).

```python
class MultiAgentOrchestrator:
    def __init__(self, role_runner: Optional[Callable[[str, OrchestrationContext], Dict[str, Any]]] = None): ...
    def run(
        self,
        goal: str,
        *,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
        inputs: Optional[Dict[str, Any]] = None,
        roles: Optional[List[str]] = None,
        max_retries: int = 2,
    ) -> AgentRunResult: ...
```

Like the v1 runtime, the orchestrator is pure logic over an injected
`role_runner` port, so it runs with no LLM and no server. `default_role_runner`
is deterministic and genuinely useful: the `researcher` pulls workspace context,
the `planner` decomposes the goal, the `executor` carries out steps (and can
drive an injected `workflow_runner` / `plugin_runner`), and the `reviewer`
returns `pass` / `retry`. The reviewer can rewind the pipeline to the executor up
to `max_retries` times; the final `status` is `ok`, `retried_ok`, or `failed`.

### 1.4 Realtime Collaboration (`latticeai.core.realtime`)

An in-process pub/sub bus, presence registry, and activity feed delivered over
Server-Sent Events (SSE). SSE is chosen deliberately: the codebase already
streams model output over SSE, it needs no extra dependency, and it works through
the existing single-port local-first deployment.

Guarantees from the module:

- **Single-user local mode keeps working.** Publishing with zero subscribers is
  a no-op; a `_FEED_LIMIT`-bounded ring buffer is still maintained so a late
  subscriber catches up via `recent()` / a replay tail on `stream()`.
- **Workspace isolation preserved.** Every event carries `workspace_id`; a
  subscriber only receives events whose workspace is in its allowed scope set.
  A `None` scope (personal / local view) sees unscoped + personal events.
- **Backpressure-safe.** Per-subscriber queues are bounded (`_QUEUE_MAX`); on
  overflow the oldest event is dropped rather than blocking the publisher.

```python
class RealtimeBus:
    def publish(self, event: Dict[str, Any]) -> Dict[str, Any]: ...
    def __call__(self, event: Dict[str, Any]) -> Dict[str, Any]:  # stable event_sink alias
        return self.publish(event)
```

---

## 2. How the Pillars Compose Into One Platform

The four pillars are not parallel silos. They are stitched into one platform by
exactly **three additive seams**, all introduced without changing any existing
behavior.

### Seam 1 — Two new state keys with deep-merge backfill

`WorkspaceOSStore._default_state()` adds two new top-level keys to the local-first
JSON state: **`plugin_registry`** (an object, mirroring `skill_registry`) and
**`workflow_runs`** (a list, alongside the existing `workflows`). The default
state also adds v2.0 feature flags (`plugin_sdk`, `workflow_designer`,
`multi_agent_runtime`, `realtime_collaboration`) and a `plugins` navigation area.

These are safe for existing data because `load_state()` runs `_deep_merge(default,
loaded)` on every load. `_deep_merge` walks the default tree and fills in any key
missing from the loaded file while preserving every value already present:

```python
def _deep_merge(default: Any, loaded: Any) -> Any:
    if isinstance(default, dict) and isinstance(loaded, dict):
        merged = {key: _deep_merge(value, loaded.get(key)) for key, value in default.items()}
        for key, value in loaded.items():
            if key not in merged:
                merged[key] = value
        return merged
    if loaded is None:
        return default
    return loaded
```

A v1.x `workspace_os.json` that has no `plugin_registry` / `workflow_runs` is
therefore upgraded *in memory* on first load — the new keys are backfilled with
their defaults, every pre-existing snapshot, trace, memory, agent run, workflow,
and skill entry is preserved, and the file is only rewritten on the next normal
`save_state`. The Plugin SDK lifecycle helpers (`list_plugin_registry`,
`set_plugin_enabled`, `mark_plugin_installed`, `mark_plugin_uninstalled`) and
workflow-run helpers (`record_workflow_run`, `list_workflow_runs`) operate on
these keys, deliberately mirroring the existing skill-registry contract.

### Seam 2 — A single `event_sink` on `record_timeline_event`

`WorkspaceOSStore.__init__` takes one optional `event_sink` hook. Every state
mutation in the store already funnels through `record_timeline_event(area,
event_type, payload, workspace_id)`. v2.0 adds a single best-effort call at the
end of that method:

```python
def record_timeline_event(self, area, event_type, payload, workspace_id=None):
    ...
    state.setdefault("timeline", []).append(event)
    state["timeline"] = state["timeline"][-500:]
    self.save_state(state)
    if self.event_sink is not None:
        try:
            self.event_sink(event)
        except Exception:
            # Realtime delivery is best-effort and must never break a write.
            pass
    return event
```

In `server_app.py` the bus is constructed first and injected as that sink:

```python
REALTIME_BUS = RealtimeBus()
WORKSPACE_OS = WorkspaceOSStore(DATA_DIR, event_sink=REALTIME_BUS)
```

Because `RealtimeBus.__call__` aliases `publish`, **every** timeline event —
workspace, snapshot, memory, graph trace, agent run, workflow run, plugin
install/enable, skill change, onboarding, presence — fans into the realtime feed
automatically. There is no per-call instrumentation and no duplicated event
system. The hook defaults to `None`, so existing callers and tests see zero
behavior change.

### Seam 3 — `PlatformRuntime` as the one cross-wiring point

`latticeai/services/platform_runtime.py` is the single place the four subsystems
cross-wire to one another and to the workspace. Keeping it out of `server_app`
honours the `AGENTS.md` preference for small, composable, independently testable
modules; `server_app` only constructs it and mounts routers.

```python
PLATFORM = PlatformRuntime(
    store=WORKSPACE_OS,
    workspace_service=WORKSPACE_SERVICE,
    plugin_registry=PLUGIN_REGISTRY,
    get_current_user=get_current_user,
    workspace_graph=_workspace_graph,
    workspace_scope_from_request=_workspace_scope_from_request,
    get_tool_permission=get_tool_permission,
)
```

`PlatformRuntime` provides:

- **Request gating** — `gate_read` / `gate_write` resolve a caller's workspace
  scope via `WorkspaceService` (raising `403` on `PermissionError`), and
  `allowed_scopes` returns the set of workspaces a user may see (used by the
  realtime router).
- **Plugin lifecycle hooks** — `register_plugin_skill` marks a bundled skill
  installed in the shared skill registry (`version="plugin:<id>"`), so plugins
  extend the existing skill surface instead of owning a parallel one.
- **Shared node runners** — `_tool_node_runner`, `_skill_node_runner`,
  `_plugin_node_runner`, `_agent_node_runner`, plus `plugin_capability_runners`
  (the runner map the Plugin SDK boundary dispatches to) and `_context_provider`
  (feeds workspace memory to the agent researcher role).
- **Cross-system runs** — `run_workflow_by_id` and `run_agent`, plus the
  factories `build_workflow_runners`, `build_orchestrator`, and
  `plugin_capability_runners` that are handed to the routers.

The four routers are wired entirely through `PLATFORM`:

```python
app.include_router(create_plugins_router(
    registry=PLUGIN_REGISTRY,
    register_skill=PLATFORM.register_plugin_skill,
    plugin_runners_factory=lambda: PLATFORM.plugin_capability_runners(None, None),
    ...
))
app.include_router(create_workflow_designer_router(
    store=WORKSPACE_OS,
    gate_read=PLATFORM.gate_read, gate_write=PLATFORM.gate_write,
    build_runners=PLATFORM.build_workflow_runners,
    ...
))
app.include_router(create_agents_router(
    store=WORKSPACE_OS,
    orchestrator_factory=PLATFORM.build_orchestrator,
    gate_read=PLATFORM.gate_read, gate_write=PLATFORM.gate_write,
    ...
))
app.include_router(create_realtime_router(
    bus=REALTIME_BUS,
    allowed_scopes=PLATFORM.allowed_scopes,
    ...
))
```

---

## 3. Cross-Integration Matrix

Because of the seams above, the subsystems can drive one another. The following
capabilities are all backed by `PlatformRuntime` runners and the engines:

| From | Can run | Wired by |
| --- | --- | --- |
| **Workflow** node | `tool`, `skill`, `plugin`, `agent` | `build_workflow_runners` → `_tool_node_runner`, `_skill_node_runner`, `_plugin_node_runner`, `_agent_node_runner` |
| **Agent** run (executor role) | `plugin`, `workflow` | `build_orchestrator` / `run_agent` inject `workflow_runner` + `plugin_runner` into `default_role_runner` |
| **Plugin** action | `skill`, `tool`, `workflow`, `agent` | `plugin_capability_runners` → `run_skill`, `run_tool`, `run_workflow`, `run_agent` |
| **Any timeline event** | Realtime feed | `event_sink` → `RealtimeBus.publish` (Seam 2) |
| **Graph entities** | Link to `workflow_runs`, `agent_runs` (and workflows, memories) | `record_workflow_run`, `record_agent_run`, `create_workflow` call `graph.ingest_event` and store the returned `graph_node_id` |
| **Workspace timeline** | Reflects all activity | every store mutation calls `record_timeline_event`; `timeline()` also re-projects snapshots, traces, agent runs, and workflows |

Concrete flows:

- **Workflow → tool/skill/plugin/agent.** The Workflow Designer `run` endpoint
  builds the runner map from `PLATFORM.build_workflow_runners(user, scope)` and
  passes it to `WorkflowEngine`. A `tool` node records the invocation plus its
  governance decision (via `get_tool_permission`) but never silently executes
  exec/destructive tools. A `plugin` node calls `registry.execute_action(...)`
  through the permission boundary. An `agent` node calls `run_agent(...,
  with_workflow=False)`.

- **Agent → plugin/workflow.** `run_agent` constructs `default_role_runner` with
  a `workflow_runner` (`run_workflow_by_id(..., with_agent=False)`) and a
  `plugin_runner` (`registry.execute_action(pid, "run_skill", ...)`), so the
  executor role can drive both. The result is persisted with
  `store.record_agent_run`.

- **Plugin → workflow/agent.** `plugin_capability_runners` exposes `run_workflow`
  (delegates to `run_workflow_by_id(..., with_agent=False)`) and `run_agent`
  (delegates to `run_agent(..., with_workflow=False)`), each gated by the
  plugin's declared permission.

- **Graph linkage.** When the Knowledge Graph is enabled, `record_workflow_run`
  ingests a `WorkflowRun` node, `record_agent_run` ingests an `AgentRun` node,
  and `create_workflow` ingests a `Workflow` node — each storing the returned
  `graph_node_id` on the record so runs are navigable from the graph and via the
  Relationship Explorer.

- **Unified timeline.** `WorkspaceOSStore.timeline()` merges live timeline events
  with re-projected snapshots, answer traces, agent runs, and workflows (and
  optional audit events), all scope-filtered, giving one chronological view of
  every subsystem's activity.

---

## 4. Recursion Bounding

Cross-system runs could in principle recurse forever (workflow runs an agent that
runs a workflow that runs an agent…). v2.0 bounds recursion **by construction** in
`PlatformRuntime`, not by a runtime depth counter:

- A workflow's **`agent` node** runs an orchestrator built *without* a workflow
  runner. In `run_agent` the workflow node path calls
  `run_agent(..., with_workflow=False)`, so `default_role_runner` receives
  `workflow_runner=None`.
- An orchestrator's **workflow runner** runs an engine built *without* an `agent`
  runner. `run_workflow_by_id(..., with_agent=False)` assembles the runner map
  with only `tool`, `skill`, and `plugin` — no `agent` key — so the
  `WorkflowEngine` skips any `agent` node it encounters.

The deepest possible chains are therefore:

```text
agent → workflow → (tool | skill | plugin)
workflow → agent → plugin
```

Independently, the `WorkflowEngine` enforces `_MAX_STEPS = 100` per run as a hard
cap against `next`-pointer cycles, and `MultiAgentOrchestrator` caps reviewer
retries via `max_retries` (clamped to 0–5 in the agents router). Together these
guarantee every cross-system run terminates.

---

## 5. HTTP Surface (v2.0 additions)

All v2.0 routes are namespaced so they never collide with existing paths
(`/plugins/registry` vs. the marketplace `/plugins/directory`; `/workflows` vs.
`/workspace/workflows`; `/agents` plural vs. the single-agent `/agent`).

**Plugin SDK** (`latticeai/api/plugins.py`): `GET /plugins/sdk`,
`GET /plugins/registry`, `GET /plugins/registry/{plugin_id}`,
`POST /plugins/validate`, `POST /plugins/install` (admin),
`POST /plugins/uninstall` (admin), `POST /plugins/enable`,
`POST /plugins/disable`, `POST /plugins/execute`.

**Workflow Designer** (`latticeai/api/workflow_designer.py`): `GET /workflows`,
`GET|POST /workflows/api/definitions`, `GET|PATCH
/workflows/api/definitions/{id}`, `POST /workflows/api/validate`,
`POST /workflows/api/definitions/{id}/run`,
`GET /workflows/api/definitions/{id}/runs`, `GET /workflows/api/runs`,
`GET /workflows/api/export/{id}`, `POST /workflows/api/import`.

**Multi-Agent Runtime** (`latticeai/api/agents.py`): `GET /agents`,
`GET /agents/api/roles`, `GET /agents/api/runs`, `POST /agents/api/run`.

**Realtime Collaboration** (`latticeai/api/realtime.py`): `GET /activity`,
`GET /realtime/stream` (SSE), `GET /realtime/feed`, `GET /realtime/presence`,
`POST /realtime/presence/join`, `POST /realtime/presence/leave`.

Representative run request/response (Workflow Designer):

```json
// POST /workflows/api/definitions/{workflow_id}/run
{ "inputs": { "steps": ["analyze", "package"] } }
```

```json
{
  "run": { "id": "workflow-run-…", "status": "ok", "workflow_id": "workflow-…" },
  "result": { "status": "ok", "step_count": 4, "timeline": [ /* per-node */ ], "outputs": {} }
}
```

```json
// POST /agents/api/run
{ "goal": "Draft v2.0 release notes", "roles": ["planner", "executor", "reviewer"], "max_retries": 2 }
```

```json
{
  "run": { "id": "agent-run-…", "agent_id": "agent:executor", "status": "ok" },
  "result": { "status": "ok", "roles_run": ["planner", "executor", "reviewer"], "retries": 0, "output": "…" }
}
```

---

## 6. Compatibility

> **Compatibility note.** v2.0.0 is **additive**. All v1.x data and APIs are
> preserved; the platform layers new capabilities on top of unchanged surfaces.

Preserved surfaces, verified against source:

- **ASGI entrypoints.** `server:app` and `latticeai.server_app.app` remain the
  application objects. `server_app.py` still exposes the module-level `app =
  FastAPI(...)` plus the `main()` / `uvicorn.run(app, ...)` entry point.
- **Version wiring.** `WORKSPACE_OS_VERSION = "2.0.0"` drives both
  `APP_VERSION` (and thus the FastAPI `app.version`) and the `/health`
  response — the health router is constructed with `app_version=APP_VERSION`.
- **Existing routes.** Every v1.x router (`auth`, `admin`, `security_dashboard`,
  `workspace`, `health`, `models`, `chat`, `tools`, `garden`, `setup`,
  `static_routes`) is still included unchanged in `server_app.py`. The v2.0
  routers are *added* alongside them under non-colliding namespaces.
- **Data.** The local-first `workspace_os.json` is upgraded by deep-merge
  backfill (Seam 1) and `_migrate_workspaces` (non-destructive workspace
  upgrade); no migration deletes or rewrites existing snapshots, traces,
  memories, agent runs, workflows, or skills.
- **Skills.** Standalone installed skills keep working; plugins reuse the same
  `skill_registry` via `register_plugin_skill` rather than a parallel store.
- **Snapshots.** Snapshot files remain immutable JSON under
  `workspace_snapshots/`; `snapshot["version"]` is stamped with
  `WORKSPACE_OS_VERSION` but creation/compare/export behavior is unchanged.
- **Knowledge Graph.** Graph operations stay additive (`ingest_event`,
  `ingest_message`); v2.0 only *adds* `WorkflowRun` / `AgentRun` linkage on top,
  honoring the `AGENTS.md` rule to preserve legacy read compatibility and avoid
  destructive migrations.
- **Legacy workflows.** Pre-2.0 workflows persisted as a flat `steps` list still
  validate and run — `workflow_engine.normalize_definition` lifts them into a
  linear `trigger → tool… → output` node chain without rewriting stored history.
- **Realtime hook is opt-in.** `WorkspaceOSStore(event_sink=...)` defaults to
  `None`; without a bus the store behaves exactly as in v1.x.

---

## 7. Per-Subsystem Reference

Each pillar is intentionally a small, self-documenting module. For the
authoritative, code-level definition of each subsystem, see:

- **Plugin SDK** — `latticeai/core/plugins.py` (manifest schema, permissions,
  execution boundary) and `latticeai/api/plugins.py` (HTTP surface).
- **Workflow Designer** — `latticeai/core/workflow_engine.py` (node types,
  validation, interpreter) and `latticeai/api/workflow_designer.py`.
- **Multi-Agent Runtime 2.0** — `latticeai/core/multi_agent.py` (roles, pipeline,
  retry) and `latticeai/api/agents.py`.
- **Realtime Collaboration** — `latticeai/core/realtime.py` (bus, presence, SSE)
  and `latticeai/api/realtime.py`.
- **Cross-wiring** — `latticeai/services/platform_runtime.py`.
- **State + persistence** — `latticeai/core/workspace_os.py`.
- **App assembly** — `latticeai/server_app.py`.
