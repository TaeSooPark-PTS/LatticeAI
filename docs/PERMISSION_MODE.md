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

## Code map

| Module | Role |
|--------|------|
| `latticeai/core/permission_mode.py` | Pure decision table |
| `latticeai/core/agent_permission.py` | Agent plan/tool gate helpers |
| `latticeai/core/agent_mode_patch.py` | Patches `SingleAgentRuntime` gates |
| `latticeai/services/permission_mode_service.py` | Persistence |
| `latticeai/services/tool_dispatch.py` | `enforce_policy` + `build_agent_runtime` |
| `latticeai/api/permission_mode.py` | HTTP routes |

## App factory wiring (required once)

Near `CHANGE_PROPOSALS` construction in `app_factory`:

```python
from latticeai.services.permission_mode_service import PermissionModeService
from latticeai.api.permission_mode import create_permission_mode_router
from latticeai.core.permission_mode import normalize_mode
import os

PERMISSION_MODES = PermissionModeService(
    data_dir=DATA_DIR,
    default_mode=normalize_mode(os.environ.get("LATTICEAI_PERMISSION_MODE", "strict")),
    audit=append_audit_event,
)
configure_tool_dispatch(
    load_users=load_users,
    get_user_role=get_user_role,
    permission_mode=lambda: PERMISSION_MODES.resolve(),
)
app.include_router(
    create_permission_mode_router(
        service=PERMISSION_MODES,
        require_user=require_user,
    )
)
# Rebuild chat agent runtime so the mode callable is attached:
CHAT_AGENT_RUNTIME = build_agent_runtime(
    ...,
    permission_mode=lambda: PERMISSION_MODES.resolve(),
)
CHAT_AGENT_RUNTIME.deps.change_governor = CHANGE_PROPOSALS
```

## AGENTS.md note

Under **strict**, the existing rule holds: never mutate existing user content
directly — stage proposals. Under **trusted** / **bypass**, the user has
explicitly raised autonomy; mutations auto-apply with audit, circuit breakers
still deny destructive system actions.
