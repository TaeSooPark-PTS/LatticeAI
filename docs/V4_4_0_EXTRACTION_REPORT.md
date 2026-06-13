# v4.4.0 Physical Extraction Report — `lattice_brain` Brain Engine

Date: 2026-06-13
Scope: complete physical extraction of the Brain Core implementation into the
standalone `lattice_brain` package. The release flow attaches only validated
v4.4.0 artifacts to GitHub; no external package registry publishing is part of
this release step.

## 1. Problem Before v4.4.0

`lattice_brain` provided an import-path contract only. The graph, memory,
context, conversation, and ingestion implementations physically lived under
`latticeai/brain/` (and `latticeai/core/`, `latticeai/services/`) and were
re-exported through `lattice_brain.*`. Importing `lattice_brain.store`
actually executed `latticeai.brain.store`, so the Brain Core could not be
used, tested, or distributed independently of the FastAPI product package.

## 2. Dependency Graph

### Before (v4.3.3)

```
lattice_brain.store/schema/retrieval/... (1-line re-exports)
        │  imports
        ▼
latticeai.brain.* (physical implementation)
        │  imports
        ├─ latticeai.services.ingestion  (memory → IngestionItem)
        │        └─ latticeai.core.hooks (dispatch_tool)
        ├─ latticeai.core.graph_curator  (projection, lazy)
        └─ kg_schema (root shim → lattice_brain.schema → latticeai.brain.schema)
latticeai.services.agent_runtime → latticeai.core.multi_agent
latticeai.services.kg_portability → lattice_brain.archive/storage (already clean)
latticeai.core.workflow_engine    (stdlib only, hosted in latticeai)
```

Net effect: `import lattice_brain.store` pulled in `latticeai`.

### After (v4.4.0)

```
latticeai (FastAPI app, routers, services, product backend)
        │  imports (one direction only)
        ▼
lattice_brain (physical Brain Engine)
  ├─ core, archive, embeddings, storage/        (already physical in v4.2+)
  ├─ graph/   _kg_common, schema, store, write_master, retrieval, discovery,
  │           documents, ingest, projection, provenance, identity, network,
  │           curator
  ├─ memory, context, conversations
  ├─ ingestion                                   (uses .runtime.hooks)
  ├─ runtime/ hooks, multi_agent, agent_runtime
  ├─ workflow
  └─ portability                                 (uses .archive/.storage/.graph.identity)
```

`lattice_brain` has zero `latticeai` imports — enforced by an import-hook test.
External (third-party/optional) deps of the package: `cryptography`, `keyring`,
`httpx`, `psycopg` (opt-in), `sqlite-vec` (opt-in), document parsers
(`pdfplumber`, `python-docx`, `openpyxl`, `python-pptx`, `PIL`) — all already
declared in `pyproject.toml`.

## 3. Files Moved (physical, via `git mv`, history preserved)

Knowledge graph → `lattice_brain/graph/`:

| From | To |
| --- | --- |
| `latticeai/brain/_kg_common.py` | `lattice_brain/graph/_kg_common.py` |
| `latticeai/brain/schema.py` | `lattice_brain/graph/schema.py` |
| `latticeai/brain/store.py` | `lattice_brain/graph/store.py` |
| `latticeai/brain/write_master.py` | `lattice_brain/graph/write_master.py` |
| `latticeai/brain/retrieval.py` | `lattice_brain/graph/retrieval.py` |
| `latticeai/brain/discovery.py` | `lattice_brain/graph/discovery.py` |
| `latticeai/brain/documents.py` | `lattice_brain/graph/documents.py` |
| `latticeai/brain/ingest.py` | `lattice_brain/graph/ingest.py` |
| `latticeai/brain/projection.py` | `lattice_brain/graph/projection.py` |
| `latticeai/brain/provenance.py` | `lattice_brain/graph/provenance.py` |
| `latticeai/brain/identity.py` | `lattice_brain/graph/identity.py` |
| `latticeai/brain/network.py` | `lattice_brain/graph/network.py` |
| `latticeai/core/graph_curator.py` | `lattice_brain/graph/curator.py` |

Memory / context / conversation:

| From | To |
| --- | --- |
| `latticeai/brain/memory.py` | `lattice_brain/memory.py` |
| `latticeai/brain/context.py` | `lattice_brain/context.py` |
| `latticeai/brain/conversations.py` | `lattice_brain/conversations.py` |

Ingestion / runtime / workflow / portability:

| From | To |
| --- | --- |
| `latticeai/services/ingestion.py` | `lattice_brain/ingestion.py` |
| `latticeai/core/hooks.py` | `lattice_brain/runtime/hooks.py` |
| `latticeai/core/multi_agent.py` | `lattice_brain/runtime/multi_agent.py` |
| `latticeai/services/agent_runtime.py` | `lattice_brain/runtime/agent_runtime.py` |
| `latticeai/core/workflow_engine.py` | `lattice_brain/workflow.py` |
| `latticeai/services/kg_portability.py` | `lattice_brain/portability.py` |

19 files moved with history; 2 new subpackage `__init__.py` files
(`lattice_brain/graph/`, `lattice_brain/runtime/`).

Import edges rewritten inside moved code (the only source changes to the
implementations):

- `graph/_kg_common.py`: `latticeai.brain.schema` → `.schema`;
  `lattice_brain.embeddings` → `..embeddings`
- `graph/projection.py`: lazy `latticeai.core.graph_curator` → `.curator`
- `graph/provenance.py`: lazy `kg_schema` → `.schema`
- `graph/network.py`: `lattice_brain.identity` → `.identity`
- `graph/store.py`: lazy `lattice_brain.storage` → `..storage`
- `memory.py`: `latticeai.services.ingestion` → `.ingestion`
- `ingestion.py`: `latticeai.core.hooks` → `.runtime.hooks`
- `runtime/agent_runtime.py`: `latticeai.core.multi_agent` → `.multi_agent`
- `portability.py`: absolute `lattice_brain.*` → relative; lazy identity →
  `.graph.identity`
- `runtime/hooks.py`: three `binding` metadata strings updated to the new
  physical paths

## 4. Re-Export Modules Deleted / Replaced

Deleted (the old `lattice_brain → latticeai.brain` re-exports — the "fake
extraction" layer):
`lattice_brain/{_kg_common,schema,store,write_master,retrieval,discovery,documents,ingest,projection,provenance,network,identity}.py`

Each path was recreated as an **internal alias shim** (`sys.modules[__name__] =
lattice_brain.graph.<name>`), so old flat imports keep working with module
identity preserved (the shim and the physical module are the same object —
singletons, `isinstance`, and monkeypatching are unaffected). These alias the
package to itself; nothing re-exports `latticeai` anymore.

## 5. Compatibility Shims Kept (latticeai side)

Deprecation shims (emit `DeprecationWarning`, alias the physical module):

- `latticeai/brain/__init__.py` (re-exports the public surface from
  `lattice_brain`)
- `latticeai/brain/{store,schema,retrieval,discovery,documents,ingest,projection,provenance,write_master,network,identity,_kg_common,memory,context,conversations}.py`

Silent alias shims (still-supported old paths):

- `latticeai/core/hooks.py` → `lattice_brain.runtime.hooks`
- `latticeai/core/multi_agent.py` → `lattice_brain.runtime.multi_agent`
- `latticeai/core/workflow_engine.py` → `lattice_brain.workflow`
- `latticeai/core/graph_curator.py` → `lattice_brain.graph.curator`
- `latticeai/services/ingestion.py` → `lattice_brain.ingestion`
- `latticeai/services/agent_runtime.py` → `lattice_brain.runtime.agent_runtime`
- `latticeai/services/kg_portability.py` → `lattice_brain.portability`

Root-level shims (`kg_schema.py`, `knowledge_graph.py`, …) continue to work
unchanged through the flat `lattice_brain.*` paths.

## 6. Consumers Updated to Import `lattice_brain` Directly

`latticeai/app_factory.py`, `latticeai/core/{agent,agent_registry,builtin_hooks}.py`,
`latticeai/api/{hooks,tools,computer_use,agents,browser,workflow_designer,chat,mcp}.py`,
`latticeai/services/{platform_runtime,run_executor,upload_service}.py`,
`p_reinforce.py`, `scripts/bump_version.py`, `scripts/wheel_smoke.py`, and all
test modules now use the physical `lattice_brain` paths. Zero non-shim
references to the old module locations remain.

## 7. New Tests

`tests/unit/test_lattice_brain_isolation.py`:

1. **`test_lattice_brain_never_imports_latticeai`** — subprocess installs a
   `sys.meta_path` finder that raises on any `latticeai` import, imports every
   `lattice_brain` module via `pkgutil.walk_packages`, resolves every lazy
   facade export, and asserts `latticeai` never entered `sys.modules`. This
   test fails if `lattice_brain` ever imports `latticeai`.
2. **`test_lattice_brain_usable_in_isolation`** — with the same import block
   active and FastAPI never started: `BrainCore` construction, ingestion
   pipeline → graph write, graph search, conversation store append/count,
   context assembly, workflow engine run, agent runtime/hooks availability,
   and an encrypted `.latticebrain` archive create → inspect → verify
   round-trip.

## 8. Packaging / Version Changes

- `pyproject.toml`: packages now include `lattice_brain.graph` and
  `lattice_brain.runtime`; version `4.4.0`.
- `scripts/wheel_smoke.py`: import matrix extended with the new subpackages
  and modules.
- `scripts/bump_version.py`: `MULTI_AGENT_VERSION` target re-pointed to
  `lattice_brain/runtime/multi_agent.py`.
- All 13 synchronized version copies bumped to `4.4.0` via
  `scripts/bump_version.py` (Python, npm, lockfiles, VSIX, Tauri, asset
  manifest, runtime constants).
- Docs updated: `ARCHITECTURE.md` (packaging note now describes the physical
  layout and the isolation guarantee), `README.md` (Brain Core claim, history,
  artifact names), `FEATURE_STATUS.md`, `SECURITY.md` (supported versions),
  `RELEASE_NOTES.md`, `RELEASE_NOTES_v4.4.0.md`, `docs/CHANGELOG.md`,
  `CHANGELOG.md` pointer.

## 9. User Data and Behavior

No storage schema, migration, archive format, or API change. The SQLite
default engine, Postgres opt-in path, `.latticebrain` archive format, and
backup/restore flows are byte-identical code, relocated. Old pickles are not
used; module identity through shims keeps any dynamic lookups working.

## 10. Validation Results

Environment note: final release validation ran locally on macOS with Python
3.12.13 (`LTCAI_PYTHON=/tmp/ltcai-v440-py312/bin/python`), Node 26.0.0,
npm 11.12.1, Rust 1.96.0, Cargo 1.96.0, and Tauri CLI 2.0.0.

| Check | Result |
| --- | --- |
| Python compile (`compileall` over `lattice_brain`, `latticeai`, `tests`, `tools`, `scripts`, root modules) | PASS |
| `ruff check .` | PASS (no findings) |
| Unit tests (`pytest tests/unit`) | **604 passed** (includes the 2 new isolation tests) |
| Integration tests (`pytest tests/integration` against live uvicorn on localhost) | 9 passed, 1 skipped (live-Postgres test, opt-in by design) |
| `lattice_brain` isolation tests | PASS (both) |
| No `lattice_brain → latticeai` import test | PASS |
| Graph/search/ingestion tests (`test_kg_*`, `test_ingestion_pipeline`, `test_browser_ingestion`, …) | PASS (within unit suite) |
| Backup/restore + `.latticebrain` archive tests (`test_kg_portability`, archive round-trip in isolation test) | PASS |
| Frontend lint (`npm run lint`) | PASS |
| Frontend typecheck (`tsc --noEmit` + VS Code extension `tsc`) | PASS |
| Playwright visual tests (`npx playwright test`) | **12 passed** |
| Tauri check (`cargo check`) | PASS (existing transitive `block v0.1.6` future-incompat warning) |
| Tauri DMG build (`npm run release:artifacts`) | PASS (`Lattice AI_4.4.0_aarch64.dmg`) |
| Release artifact validation (`scripts/validate_release_artifacts.py 4.4.0 --require-vsix --require-tgz --require-dmg`) | PASS (warns about historical versions in `dist/`, as designed) |
| Wheel smoke | PASS (29 wheel modules import from the wheel; `/health` returns `4.4.0`) |
| `npm pack --dry-run` | PASS (315 files) |

## 11. v4.4.0 Artifacts Built

- `dist/ltcai-4.4.0-py3-none-any.whl` (contains `lattice_brain/graph/*`,
  `lattice_brain/runtime/*`; verified by wheel content check + smoke)
- `dist/ltcai-4.4.0.tar.gz`
- `ltcai-4.4.0.tgz` (npm pack)
- `dist/ltcai-4.4.0.vsix` (built via `scripts/build_vsix.mjs`)
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.4.0_aarch64.dmg`

Per instructions: GitHub Release attachment uses only these exact validated
v4.4.0 artifacts. External package registries are not published.
