# Router Reorder Analysis Before Step 3

Branch: `feat/v6-app-factory-decomposition`
Baseline commit: `b878539`

## Snapshot

- Route snapshot file: `output/v6-app-factory-decomposition/route-snapshot-before-reorder.txt`
- Top-level route entries: 364
- Static mounts are currently:
  - `004 MOUNT /static name=static`
  - `005 MOUNT /icons name=icons`

## Movable Blocks

These can move into a router assembly helper once their dependencies are passed
through an explicit context object. They are mostly pure router includes:

- Static routes: lines 1216-1226
- Auth router: lines 1228-1241
- Admin and invitations: lines 1243-1281
- Security dashboard: lines 1283-1319
- Workspace router: lines 1327-1439
- Plugin/workflow/agents/marketplace/realtime routers: lines 1518-1576
- Health/models/chat/search/tools/hooks/agent registry/memory routers:
  lines 1585-1701
- Review queue/browser/portability/network/garden/setup routers: lines 1703-1747

## Not Yet Movable Without Reordering

These blocks are interleaved with router includes and create services required
by later routers. Move them only after route snapshot comparison:

- `SEARCH_SERVICE`, `BRAIN_MEMORY`, `CONTEXT_ASSEMBLER`: lines 1354-1369
- `context = AppContext(...)`: lines 1382-1436
- `PLATFORM = PlatformRuntime(...)`: lines 1455-1467
- `REVIEW_QUEUE`, `TRIGGER_SERVICE`, `AGENT_RUNTIME`, `RUN_EXECUTOR`:
  lines 1469-1508
- `MODEL_SERVICE`: lines 1585-1589
- `_embedding_info`, `_allowed_workspaces_for`: lines 1633-1647
- `_run_review_item`: lines 1705-1713
- `BRAIN_NETWORK`: lines 1735-1739

## Required Context Fields For Router Assembly

Minimum fields needed before `register_routers(app, context)` can exist:

- App shell: `app`, `STATIC_ROUTES`, `ui_file_response`, `local_sysinfo`
- Auth/admin/security: `load_users`, `save_users`, `require_user`,
  `require_admin`, `get_current_user`, `get_user_role`, `append_audit_event`,
  `get_audit_log`, `public_sso_config`, `save_sso_config`, invitation and
  policy helpers
- Workspace/platform: `AppContext`, `WORKSPACE_OS`, `WORKSPACE_SERVICE`,
  `PLATFORM`, `SEARCH_SERVICE`, `MEMORY_SERVICE`, `CONTEXT_ASSEMBLER`,
  `BRAIN_MEMORY`, `REALTIME_BUS`
- Automation/runtime: `HOOKS_REGISTRY`, `AGENT_REGISTRY`, `AGENT_RUNTIME`,
  `RUN_EXECUTOR`, `TRIGGER_SERVICE`, `REVIEW_QUEUE`
- Model/engine: `MODEL_SERVICE`, `router`, `runtime_features`, `engine_status`,
  model load/prepare/install functions, cloud verification helpers
- Brain/tools: `INGESTION_PIPELINE`, `KNOWLEDGE_GRAPH`, `LOCAL_KG_WATCHER`,
  `KG_PORTABILITY`, `DEVICE_IDENTITY`, `BRAIN_NETWORK`
- Static/config: `CONFIG`, `DATA_DIR`, `STATIC_DIR`, `BASE_DIR`, `APP_MODE`,
  `APP_VERSION`, `DEFAULT_PORT`, feature flags

## Recommendation

Do not extract all router includes in one patch. First extract a construction
context builder that creates `SEARCH_SERVICE`, `CONTEXT_ASSEMBLER`,
`PLATFORM`, `REVIEW_QUEUE`, `TRIGGER_SERVICE`, `AGENT_RUNTIME`, `RUN_EXECUTOR`,
`MODEL_SERVICE`, and `BRAIN_NETWORK` before any router registration. Then move
router includes into a `register_routers(app, context)` helper without changing
the include order.
