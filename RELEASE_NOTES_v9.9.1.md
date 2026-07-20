# Lattice AI v9.9.1 — Clean Foundations

Release date: 2026-07-21

9.9.1 pays down the debt that made the codebase harder to trust and the
product harder to love: the legacy root-shim layer is physically gone, the
first five minutes of a new user's experience now demonstrate real value, the
Review Center reads like a product instead of a debug view, and every major
error and empty state speaks plain Korean or English. A new legacy debt gate
and an evidence retention policy keep both kinds of cleanliness permanent.

## Highlights

### The root shim layer is gone (92% of tracked shims removed)

- 12 of the 13 tracked legacy shims were physically deleted: `ltcai_cli.py`,
  `auto_setup.py`, `setup_wizard.py`, `mcp_registry.py`, `kg_schema.py`,
  `knowledge_graph.py`, `knowledge_graph_api.py`, `local_knowledge_api.py`,
  `llm_router.py`, `p_reinforce.py`, `telegram_bot.py`, and the root `tools/`
  package. Only `server.py` remains, because `uvicorn server:app`, the Docker
  CMD, and launch scripts are external contracts.
- Canonical imports are now the physical module paths (`latticeai.*`,
  `lattice_brain.*`). The console script `LTCAI` targets
  `latticeai.cli.entrypoint:main`; `bin/ltcai.js` and the Electron shell spawn
  `python -m latticeai.cli.entrypoint`.
- Packaging follows: `pyproject.toml` ships a single root module (`server`),
  and the npm tarball / wheel no longer contain shim files. The wheel smoke
  test asserts removed shims are *absent* from the installed package.
- The managed inventory in `latticeai.core.legacy_compatibility` records all
  twelve removals (`removed-9.9.1`), so tooling can tell "removed on purpose"
  from "missing by accident".

### Legacy debt gate

- `scripts/check_legacy_debt.mjs` runs in `npm run lint`: it fails if a
  Python module other than `server.py` appears at the repo root, if a removed
  shim directory reappears, or if any source tree imports a removed shim.
- `tests/unit/test_legacy_root_shims.py` was rewritten as the runtime half of
  the gate: removed shims must stay unimportable, canonical replacements must
  import, and the registry must match reality.

### First five minutes that show value

- A guided **"First 5 minutes"** card now sits on the empty Brain home with
  three real actions: ask a starter question (fills the composer), add a
  first file or note (focuses the ingestion dock), and see what the Brain
  learned (opens insights). Progress persists in `localStorage`, steps
  complete automatically from real product signals, and the card disappears
  once done or dismissed.
- **Today's briefing** moved out of a double-collapsed drawer onto the Brain
  home, fetches immediately, and degrades to one friendly line when there is
  nothing to show — never raw errors.
- **Cmd+K opens proactively**: before typing, the palette offers "open
  today's briefing", "review pending items" (with live counts), and "ask your
  Brain".

### Review Center at product quality

- Proposal and review cards translate `effective_status`, source, risk, and
  change class into human ko/en labels; raw workflow/trigger/run IDs moved
  into a collapsed "Technical details" disclosure.
- Diffs are framed (header, file target, +/- line coloring) and honest about
  truncation ("N more lines" instead of a silent cut).
- The pending-proposals panel gained a real error state with retry — a failed
  fetch no longer masquerades as "no proposals".
- Action failures show a friendly message first; raw backend detail is
  demoted to secondary text.

### Errors that speak your language

- The frontend API error pipeline is localized: timeouts, unreachable local
  service, and HTTP failures produce plain-language ko/en copy instead of raw
  `statusText` or hardcoded English. Chat save/download, admin, care, and
  intelligence panels follow the same "friendly first, technical second"
  rule, and the intelligence panel now distinguishes "failed to load" from
  "all zeros".

### Scenario-first test suite

- 37 version-named test files (`test_v3_*` through `test_v78_*`, `test_t4_*`
  through `test_t9_*`, `test_kg_v2/v4_*`, `*_v36`, `*_v14`) were renamed to
  behavior-named suites (`test_trust_gates.py`, `test_kg_temporal.py`,
  `test_conversation_store.py`, …) and versioned function names cleaned up.
  Coverage is unchanged: 1284 unit tests, 13 integration tests.

### Release evidence retention policy

- `output/release/` keeps the newest three versioned evidence directories
  (~70MB reclaimed immediately). `npm run release:evidence` prunes
  automatically via `scripts/prune_release_evidence.mjs`
  (`LTCAI_RELEASE_EVIDENCE_KEEP` to adjust); older evidence is reproducible
  from its tag.

### Documentation 100% in sync, and kept there

- `docs/LEGACY_COMPATIBILITY.md` rewritten for the post-shim layout;
  `docs/kg-schema.md` points at `lattice_brain/graph/schema.py`; the
  ARCHITECTURE.md Release Artifact Map and lingering 9.8.0-era references are
  current.
- `scripts/check_current_release_docs.mjs` now also verifies the ARCHITECTURE
  artifact map names current-version artifacts exactly — the drift class this
  release fixed can no longer slip through.

## Verification

- Unit: 1284 passed. Integration: 13 passed. Frontend: 47 vitest tests
  passed. Ruff, Python syntax discovery (1160 modules), OpenAPI drift, i18n
  literal gate, bundle budget (143.6 KiB gzip ≤ 150 KiB), legacy debt gate,
  agent-loop eval (20/20), brain quality eval, and product readiness all
  pass.

## Artifacts (exact names)

- `dist/ltcai-9.9.1-py3-none-any.whl`
- `dist/ltcai-9.9.1.tar.gz`
- `ltcai-9.9.1.tgz`
- `dist/ltcai-9.9.1.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_9.9.1_aarch64.dmg`
