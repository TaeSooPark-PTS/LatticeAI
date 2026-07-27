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

## Wiring (automatic)

No manual `app_factory` edits required. On startup:

1. `build_chat_agent_runtime_from_context` binds dispatch + agent to the
   shared mode resolver.
2. `register_review_and_brain_tail_routers` mounts `/api/permission-mode`
   and initializes `PermissionModeService` with the real `data_dir` + audit.

## Code map

| Module | Role |
|--------|------|
| `latticeai/core/permission_mode.py` | Pure decision table |
| `latticeai/core/agent_permission.py` | Agent plan/tool gate helpers |
| `latticeai/core/agent_mode_patch.py` | Patches `SingleAgentRuntime` gates |
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
