# Lattice AI v6.1.0 Architecture Review

## Current Direction

The v6.1.0 line keeps Lattice AI centered on a local-first Digital Brain:
knowledge and provenance are durable, while models and automation runs are
replaceable execution layers.

## Review Center Boundary

Implemented boundaries:

- `latticeai/services/review_queue.py` owns Review Center policy:
  transitions, read-time `effective_status`, `run_now` back-linking, and
  unsnooze semantics.
- `latticeai/api/review_queue.py` is a thin FastAPI router with explicit
  response models.
- `WorkspaceOSStore` owns workspace-scoped persistence.
- `TriggerService` and `RunExecutor` enqueue review items only through the
  opt-in `review_queue: true` path.

This keeps Review Center behavior additive and avoids changing legacy workflow
execution unless a workflow opts in.

## App Factory

Status:

- `app_factory.py` still owns final orchestration, but broad runtime
  construction now sits behind additive runtime seams.
- `latticeai/runtime/bootstrap.py` owns session store construction and token
  lifecycle helper closures.
- `latticeai/runtime/hooks_runtime.py` owns hook registry/watcher assembly plus
  trigger/builtin hook runner binding.
- `latticeai/runtime/web_runtime.py` owns FastAPI shell creation, CORS
  middleware, and static asset mounts while preserving legacy mount order.
- `latticeai/runtime/persistence_runtime.py` owns WorkspaceOS, workspace
  service, realtime bus, plugin/template/agent registries, memory service,
  ingestion pipeline, device identity, and KG portability construction.
- `latticeai/runtime/lifespan_runtime.py` owns startup/shutdown background
  tasks, model autoload/idle unload loops, Telegram bridge startup, watcher
  restore/stop, and local model process cleanup.
- `latticeai/runtime/automation_runtime.py` owns Review Queue, TriggerService,
  AgentRuntime, and RunExecutor construction behind one automation seam.
- `latticeai/runtime/context_runtime.py` owns SearchService, BrainMemory, and
  ContextAssembler construction behind one retrieval/context seam.
- `latticeai/runtime/app_context_runtime.py` owns construction of the typed
  `AppContext` dependency object consumed by API routers.
- `latticeai/runtime/platform_services_runtime.py` owns small platform service
  constructors such as ModelService and BrainNetwork.
- `latticeai/runtime/router_registration.py` centralizes the individual
  `include_router` call behind registration helpers while preserving existing
  include order. Static route support, auth/admin/security/workspace router generation,
  early static/auth/admin/security/workspace route registration, platform
  feature routes, health/model routes, interaction routes, and final
  review/browser/brain tail routes now use dedicated helpers while preserving
  the legacy construction dependencies.
- Router generation has moved behind dependency-boundary helpers, and
  `app_factory.py` no longer directly calls `create_*router`,
  `register_router`, or `register_routers`. Further extraction should focus on
  any remaining orchestration-only code before changing include order.

Recommended next steps:

- Extract any remaining app-factory orchestration-only code into focused
  runtime seams where it improves testability without hiding bootstrap order.
- Continue the existing runtime split pattern rather than introducing a new
  global registry.
- Preserve lazy imports and current app bootstrap semantics.

## Brain Core Boundary

Expected rule:

- `lattice_brain` must not import `latticeai`.

Verification command:

```bash
rg "from latticeai|import latticeai" lattice_brain
```

Current finding:

- AST import scan and `tests/unit/test_lattice_brain_isolation.py` pass.
- `rg` reports `latticeai` strings in `lattice_brain/runtime/__init__.py`, but
  those are architecture-map docstring references, not executable imports.

## Compatibility Risks

- Review queue persistence adds state but does not require destructive
  migration.
- `unsnooze` only mutates explicitly through an API action.
- Snooze expiry remains read-time only, so no scheduler mutation or hidden
  migration is introduced.

## Architecture Score Evidence

Positive evidence:

- Review Center policy is testable independently from the API.
- Frontend Review Center is feature-owned instead of embedded inside `Act.tsx`.
- OpenAPI-generated schemas and operation paths now drive ReviewItem frontend
  typing and review action calls.
- Lifespan and persistence assembly are tested behind focused runtime seams.

Remaining gaps:

- `app_factory.py` decomposition remains incomplete, but router assembly is now
  routed through helper seams with the frozen route/mount snapshot preserved.
  Remaining work is lower-level orchestration cleanup, not route generation.
- Strict generated client methods are now in place for Review Center; other API
  domains still use the local `apiJson` wrapper.
