# Legacy Compatibility Map

Current target: **9.9.1 — Legacy debt paydown**.

Lattice AI is centered on `lattice_brain`, `latticeai.services`, `latticeai.api`,
and `latticeai.runtime`. 9.9.1 completed the root-shim compatibility window:
every repo-root import shim except `server.py` was physically deleted, and a
legacy debt gate keeps the root clean.

The managed inventory lives in `latticeai.core.legacy_compatibility` and
groups shims by layer:

- `root`: historical repo-root imports such as `knowledge_graph.py` —
  **removed in 9.9.1** except `server.py` (external entrypoints:
  `uvicorn server:app`, Docker CMD, launch scripts).
- `brain-flat`: pre-graph-package imports such as `lattice_brain.store` —
  **removed in 8.8.0** (internal-only).
- `deprecated-namespace`: older `latticeai.brain.*` package imports —
  **removed in 8.8.0** (internal-only).
- `service-alias`: `latticeai.services.agent_runtime` —
  **removed in 8.8.0** (internal-only).

Removed layers stay listed in `legacy_compatibility.REMOVED_SHIMS` and in the
`legacy_shim_report()` payload (`removed`, `lingering`), so tooling can tell
"removed on purpose" apart from "missing by accident".

## Legacy Debt Gate

**One** gate keeps the debt paid down:

- `tests/unit/test_legacy_root_shims.py` — asserts removed shims stay
  unimportable and canonical replacements import.

There used to be two. `scripts/check_legacy_debt.mjs` stated the same rule in
JavaScript as part of `npm run lint`, and by 11.8.0 the two implementations had
drifted apart — at which point having both was worse than having one, because
neither could be trusted as the answer. The Python test is authoritative and the
mjs mirror was deleted. `latticeai.core.legacy_compatibility` went with the
platform code in 11.6.0; the registry it held described modules that no longer
exist to shim.

## Root Module Map (removed in 9.9.1)

| Removed root module | Canonical import |
| --- | --- |
| `knowledge_graph.py` | `from lattice_brain.graph.store import KnowledgeGraphStore` |
| `knowledge_graph_api.py` | `from latticeai.api.knowledge_graph import create_knowledge_graph_router` |
| `kg_schema.py` | `from lattice_brain.graph.schema import ...` |
| `auto_setup.py` | `from latticeai.setup.auto_setup import probe, recommend` |
| `llm_router.py` | `import latticeai.models.router` |
| `ltcai_cli.py` | `from latticeai.cli.entrypoint import main` (console script `LTCAI`) |
| `mcp_registry.py` | `import latticeai.core.mcp_registry` |
| `local_knowledge_api.py` | `from latticeai.services.local_knowledge import create_local_knowledge_router` |
| `p_reinforce.py` | `from latticeai.services.p_reinforce import PReinforceGardener` |
| `telegram_bot.py` | `from latticeai.integrations.telegram_bot import run_bot` |
| `setup_wizard.py` | `from latticeai.setup.wizard import scan_environment` |
| `tools/` | `from latticeai.tools import execute_tool` |

The only remaining root module:

| Kept root module | Why it remains |
| --- | --- |
| `server.py` | Lazy proxy to `latticeai.server_app`; preserves `uvicorn server:app`, the Docker CMD, and launch scripts without import-time construction |

## Brain Core extraction readiness

`lattice_brain` has exactly one import surface (its physical module paths).
Two structural guards keep it extractable as a standalone package:

- `tests/unit/test_brain_core_isolation.py` — AST guard that fails if any
  `lattice_brain` module imports `latticeai` (the product imports the Brain,
  never the reverse).
- `tests/unit/test_legacy_root_shims.py::test_internal_shim_layers_are_gone`
  — fails if a removed shim path becomes importable again.

## Inner Shim Layers (removed in 8.8.0)

| Legacy layer | Example module | Replacement import | Status |
| --- | --- | --- | --- |
| `brain-flat` | `lattice_brain.store`, `lattice_brain.ingest`, `lattice_brain.retrieval` | `lattice_brain.graph.*` | Removed — internal-only, no supported entrypoint depended on it |
| `deprecated-namespace` | `latticeai.brain.*` | `lattice_brain.*` | Removed — namespace also dropped from `pyproject.toml` packages |
| `service-alias` | `latticeai.services.agent_runtime` | `lattice_brain.runtime.agent_runtime` | Removed — runtime ownership sits in Brain Core |

## Packaging Notes

`pyproject.toml` ships a single root module (`server`) plus the
`latticeai.*`/`lattice_brain.*` packages; the npm package mirrors that. The
console script `LTCAI` targets `latticeai.cli.entrypoint:main` directly, and
`bin/ltcai.js` spawns `python -m latticeai.cli.entrypoint`.

## Removal Checklist

Before removing or excluding a legacy module:

1. Search imports in the repository and generated package files.
2. Add or update a compatibility test.
3. Confirm the package still imports from a fresh non-repo working directory.
4. Update `README.md`, `ARCHITECTURE.md`, `FEATURE_STATUS.md`, and this file.
5. Run unit tests, package build, and wheel smoke validation.
