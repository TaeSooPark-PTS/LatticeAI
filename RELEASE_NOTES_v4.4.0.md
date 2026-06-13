# Lattice AI v4.4.0 - Brain Engine Extraction Release

Release date: 2026-06-13

v4.4.0 completes the physical extraction of the Brain Core into the standalone
`lattice_brain` package. Before this release, `lattice_brain` was an
import-path contract: graph, memory, context, conversation, and ingestion
modules physically lived under `latticeai/brain/` and were re-exported. As of
v4.4.0 the implementation physically lives in `lattice_brain`, and the
`latticeai` package keeps only thin compatibility shims.

## Highlights

- **Physical extraction, not re-export.** The knowledge graph
  (`lattice_brain.graph`: schema, store, write/retrieval/discovery/documents/
  ingest/projection/provenance mixins, device identity, brain network, graph
  curator), memory, context assembler, conversation store, unified ingestion
  pipeline, hook + multi-agent + agent runtime (`lattice_brain.runtime`),
  workflow engine, and backup/restore portability service now physically live
  in `lattice_brain` alongside the existing core, archive, embeddings, and
  storage modules.
- **Hard isolation guarantee.** `lattice_brain` never imports `latticeai`.
  A new test (`tests/unit/test_lattice_brain_isolation.py`) installs an import
  hook that fails loudly on any `latticeai` import while importing every
  `lattice_brain` module, and a second test exercises graph ingest/search,
  conversations, context assembly, workflow runs, the agent runtime, and an
  encrypted `.latticebrain` archive round-trip in a subprocess with
  `latticeai` blocked and FastAPI never started.
- **Compatibility preserved.** `latticeai.brain.*` emits a
  `DeprecationWarning` and aliases the physical modules;
  `latticeai.core.hooks`, `latticeai.core.multi_agent`,
  `latticeai.core.workflow_engine`, `latticeai.core.graph_curator`,
  `latticeai.services.ingestion`, `latticeai.services.agent_runtime`, and
  `latticeai.services.kg_portability` remain as silent aliases. Old flat
  `lattice_brain.store`-style paths keep working through internal aliases.
  Module identity is preserved (the shim and the physical module are the same
  object), so singletons, `isinstance`, and monkeypatching are unaffected.
- **No data or behavior changes.** SQLite/Postgres storage layouts, migrations,
  backups, restore, `.latticebrain` archives, graph/search/ingestion behavior,
  and the FastAPI API surface are unchanged. FastAPI now imports
  `lattice_brain` directly everywhere.

## Compatibility

- `latticeai.brain` imports continue to work for this compatibility window but
  warn with `DeprecationWarning`; migrate to `lattice_brain`.
- No action is required for user data. Brains, archives, and backups created
  with v4.2.0–v4.3.3 load unchanged.

## Artifacts

- `dist/ltcai-4.4.0-py3-none-any.whl`
- `dist/ltcai-4.4.0.tar.gz`
- `ltcai-4.4.0.tgz`
- `dist/ltcai-4.4.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.4.0_aarch64.dmg`

## Validation

See [docs/V4_4_0_EXTRACTION_REPORT.md](docs/V4_4_0_EXTRACTION_REPORT.md) for
the full physical-extraction report (files moved, shims kept, dependency graph
before/after) and validation results.

## External Publishing

This release flow creates an annotated tag and GitHub Release with only the
validated v4.4.0 artifacts attached. External package registries are not
published in this step (`twine upload`, `npm publish`, `vsce publish`, and
`ovsx publish` are not run).
