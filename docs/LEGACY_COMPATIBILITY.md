# Legacy Compatibility Map

Current target: **8.9.0 — Scoped Memory & Tool Policy Hardening**.

Lattice AI is moving toward a smaller, modular architecture centered on
`lattice_brain`, `latticeai.services`, `latticeai.api`, and `latticeai.runtime`.
Some root-level modules remain packaged for compatibility with older imports,
CLI entrypoints, or extension workflows. Their presence does not define the
current architecture.

The managed inventory lives in `latticeai.core.legacy_compatibility` and
groups shims by layer:

- `root`: historical repo-root imports such as `knowledge_graph.py` — **kept**
  (external entrypoints: `uvicorn server:app`, installed CLI, old scripts).
- `brain-flat`: pre-graph-package imports such as `lattice_brain.store` —
  **removed in 8.8.0** (internal-only).
- `deprecated-namespace`: older `latticeai.brain.*` package imports —
  **removed in 8.8.0** (internal-only).
- `service-alias`: `latticeai.services.agent_runtime` —
  **removed in 8.8.0** (internal-only).

Removed layers stay listed in `legacy_compatibility.REMOVED_SHIMS` and in the
`legacy_shim_report()` payload (`removed`, `lingering`), so tooling can tell
"removed on purpose" apart from "missing by accident".

## Brain Core extraction readiness

Removing the internal layers gives `lattice_brain` exactly one import surface
(its physical module paths). Two structural guards keep it extractable as a
standalone package:

- `tests/unit/test_brain_core_isolation.py` — AST guard that fails if any
  `lattice_brain` module imports `latticeai` (the product imports the Brain,
  never the reverse).
- `tests/unit/test_legacy_root_shims.py::test_internal_shim_layers_are_gone`
  — fails if a removed shim path becomes importable again.

## Current Policy

- Keep compatibility shims while public imports or package entrypoints still
  depend on them.
- Prefer moving implementation into focused packages before removing a root
  module.
- Add deprecation notes before removal.
- Avoid breaking package users during a minor release.
- Do not silently remove rollback, backup, restore, or migration paths.
- Track every remaining shim through `latticeai.core.legacy_compatibility` so
  release readiness can report owner, replacement, reason, removal phase, and
  missing files.

## Root Module Map

| Legacy root module | Current home / direction | Why it remains |
| --- | --- | --- |
| `knowledge_graph.py` | `lattice_brain.graph` / `lattice_brain.knowledge` | Compatibility for older graph imports and historical tooling |
| `knowledge_graph_api.py` | `latticeai.api.memory`, `latticeai.api.search`, graph-related API routers | Compatibility for older API import paths |
| `kg_schema.py` | `lattice_brain` storage/schema modules | Compatibility for graph schema references |
| `auto_setup.py` | setup/model recommendation services | Compatibility for zero-config setup probes and historical auto-setup commands |
| `llm_router.py` | `latticeai.models.router` | Compatibility for older local model routing imports |
| `ltcai_cli.py` | package console entrypoint (`ltcai`) | Compatibility for the installed CLI contract |
| `mcp_registry.py` | `latticeai.core.mcp_registry` and service-backed registries | Compatibility for MCP/skills lookup entrypoints |
| `local_knowledge_api.py` | `lattice_brain.ingestion`, workspace capture APIs | Compatibility for local folder/file watcher flows |
| `p_reinforce.py` | gardener/maintenance service direction | Compatibility for existing Brain gardening runtime hooks |
| `telegram_bot.py` | opt-in integration package or disabled-by-default connector | Compatibility only; Telegram must remain opt-in |
| `setup_wizard.py` | setup and model recommendation services | Compatibility for first-run recommendation calls |
| `server.py` | lazy proxy to `latticeai.server_app` / `latticeai.app_factory` | Preserves historical `server.app` imports without import-time construction |

## Inner Shim Layers (removed in 8.8.0)

| Legacy layer | Example module | Replacement import | Status |
| --- | --- | --- | --- |
| `brain-flat` | `lattice_brain.store`, `lattice_brain.ingest`, `lattice_brain.retrieval` | `lattice_brain.graph.*` | Removed — internal-only, no supported entrypoint depended on it |
| `deprecated-namespace` | `latticeai.brain.*` | `lattice_brain.*` | Removed — namespace also dropped from `pyproject.toml` packages |
| `service-alias` | `latticeai.services.agent_runtime` | `lattice_brain.runtime.agent_runtime` | Removed — runtime ownership sits in Brain Core |

## Packaging Notes

`pyproject.toml` and `package.json` still include several root modules because
older installed packages may import them directly. That is intentional for now.
The long-term target is:

- move implementation into `lattice_brain`, `latticeai.core`, `latticeai.models`,
  `latticeai.services`, or `latticeai.api`;
- leave thin shims with docstrings/deprecation warnings;
- remove a shim only after tests prove no supported entrypoint relies on it.

## Removal Checklist

Before removing or excluding a legacy module:

1. Search imports in the repository and generated package files.
2. Add or update a compatibility test.
3. Confirm the package still imports from a fresh non-repo working directory.
4. Update `README.md`, `ARCHITECTURE.md`, `FEATURE_STATUS.md`, and this file.
5. Run unit tests, package build, and wheel smoke validation.
