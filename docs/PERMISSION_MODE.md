# Permission Mode (v9.9.8)

Frontier agents expose an autonomy dial. LatticeAI maps the same idea onto
ToolRegistry + Change Governor without discarding fail-closed defaults.

## Modes

| Mode | Workspace writes | Knowledge reads | Exec / desktop control | Mutations |
|------|------------------|-----------------|------------------------|-----------|
| **strict** (default) | approval / proposal | gated | gated | Review proposals |
| **trusted** | auto | auto | gated | auto-apply + audit |
| **bypass** | auto | auto | auto (workspace) | auto-apply + audit |

Hard circuit breakers always apply: destructive risk, `rm -rf /` / `~`,
blocked path prefixes, binary overwrite without proposal support.

Under **trusted** / **bypass** the governed mutation path is decided *before*
`ChangeGovernor.review` runs. That call persists a proposal as a side effect, so
reviewing first and discarding the verdict afterwards would apply the change
*and* leave an orphan proposal pending in the Review Center.

## API

```http
GET  /api/permission-mode
GET  /api/permission-mode/catalog
POST /api/permission-mode
{"mode": "trusted"}
{"mode": "bypass", "acknowledge_risk": true}
```

Scope: optional `workspace_id` query/body or `X-Workspace-Id` header.
Per-workspace overrides per-user; both override the process default.

Env default: `LATTICEAI_PERMISSION_MODE=strict|trusted|bypass`.

## UI

`PermissionModePanel` renders in **환경설정 → 에이전트 자율성** (`SystemPage`
settings tab). It renders the catalog `/api/permission-mode` returns rather
than a hardcoded mode list, so adding or renaming a mode server-side needs no
frontend change, and it keeps the apply button disabled until

* a *different* mode is selected, and
* for a mode whose catalog entry sets `requires_ack`, the risk acknowledgement
  is ticked — the same condition the server enforces, so the UI never sends a
  request it knows will be refused.

A failed change is surfaced with the server's own message; the panel never
reports success it did not get.

## Scope resolution

Scope is not cosmetic — it is what makes a stored override take effect. Every
enforcement point resolves the dial *with the caller's identity*:

| Enforcement point | Scope passed |
|-------------------|--------------|
| `ToolDispatchService.enforce_policy` | `current_user` + `workspace_id` |
| `SingleAgentRuntime` tool gate | `current_user` + `req.workspace_id` |
| Agent plan gate | the run's stamped mode (see below) |

`chat_agent_http` resolves the mode **once per run** and stamps it on
`AgentRunContext.permission_mode`, so the plan the user approved and every tool
step in that run are judged by the same dial even if the stored preference
changes mid-run. A paused approval run persists that stamp, so it resumes under
the mode it was approved with.

A resolver bound into `ToolDispatchService.permission_mode` may accept
`user_email`/`workspace_id` kwargs (preferred) or take no arguments; see
`call_mode_source`. An unscoped resolver always returns the process default —
which would make per-user and per-workspace overrides silently inert.

## Wiring (automatic)

No manual `app_factory` edits required. On startup:

1. `build_chat_agent_runtime_from_context` binds dispatch + agent to the
   shared mode resolver.
2. `register_review_and_brain_tail_routers` mounts `/api/permission-mode`
   and rebinds `PermissionModeService` onto the real `data_dir` + audit sink.

Step 2 *rebinds* rather than "first caller wins": a tool dispatch that happens
before routers are mounted would otherwise pin the store to the fallback
`~/.ltcai` path with no audit sink.

## Code map

| Module | Role |
|--------|------|
| `latticeai/core/permission_mode.py` | Pure decision table |
| `latticeai/core/agent_permission.py` | Agent plan/tool gate helpers |
| `latticeai/core/agent.py` | `SingleAgentRuntime` gates (mode-aware in-line) |
| `frontend/src/components/PermissionModePanel.tsx` | Settings selector |
| `latticeai/services/permission_mode_service.py` | Persistence |
| `latticeai/runtime/permission_mode_wiring.py` | Process-wide service + router mount |
| `latticeai/runtime/chat_wiring.py` | Agent runtime injection |
| `latticeai/runtime/router_registration.py` | HTTP mount |
| `latticeai/services/tool_dispatch.py` | `enforce_policy` + `build_agent_runtime` |
| `latticeai/api/permission_mode.py` | HTTP routes |

## AGENTS.md note

Under **strict**, the existing rule holds: never mutate existing user content
directly — stage proposals. Under **trusted** / **bypass**, the user has
explicitly raised autonomy; mutations auto-apply with audit, circuit breakers
still deny destructive system actions.
