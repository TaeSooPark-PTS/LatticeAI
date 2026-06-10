# Lattice AI v4.0.0 — Implementation Plan

Companion to `docs/V4_BRAIN_ARCHITECTURE.md`. Audit evidence in
`docs/v4-audit/*.json`. Program state in `docs/V4_DIGITAL_BRAIN_RECOVERY.md`.

## Ground rules (every track)

- Branch: `feat/v4-digital-brain` only. Conventional commits. Commit only
  verified work; one commit (or a few) per track.
- Gate: `.venv/bin/python -m pytest tests/unit -q` green (baseline 455 →
  grows; zero regressions), plus the new tests each track ships. The 9
  pre-existing integration failures (need a live server) are not the gate but
  must not grow.
- Python 3.11-compatible syntax (no nested same-quote f-strings). `.venv` is
  3.14 — fine for running, but write for 3.11.
- **No placeholder code, no demo data, no fabricated numbers, no
  silently-skipped work.** A capability that cannot complete must surface an
  explicit state (`simulation`, `awaiting_approval`, `unavailable`), never a
  fake success.
- Data safety: every schema migration is preceded by an automatic backup
  (existing `kg_portability` machinery) and is idempotent + re-entrant.
  User data directories (`~/.ltcai`, `~/.ltcai-brain`) are never deleted —
  absorptions import, then deactivate the old writer, leaving source files
  in place.
- Update `docs/V4_DIGITAL_BRAIN_RECOVERY.md` after every track (status,
  files touched, validation result) and before any foreseeable session limit.

## Track sequence and ownership

Tracks are sequenced by dependency; file ownership is disjoint per track so
tracks can't conflict. T1/T2 unblock everything; T3→T6 are the brain spine;
T7/T8 build on the spine; T9 (frontend) is independent of T3-T8 except for
new API names; T10 closes.

---

### T1 — Truth & safety floor  *(small; first because every later track builds on honest primitives)*

Owns: `latticeai/api/workspace.py`, `latticeai/core/workspace_os.py`
(summary/leak only), `latticeai/api/chat.py` (context filter only),
`latticeai/services/memory_service.py`, `latticeai/core/multi_agent.py` +
`latticeai/services/agent_runtime.py` (mode labeling only),
`static/v3/js/views/hybrid-search.js`, `README.md` (claims only).

1. Close by-id authz gaps: snapshot get/area/export/compare + memory delete
   gate through `resolve_read_scope`/`resolve_write_scope` using the record's
   own workspace_id; ownership check on memory delete.
2. Strip the unfiltered `workspaces` key from `/workspace/os` (keep the
   membership-filtered `workspace_registry`).
3. Fix `build_recent_chat_context` leak: scope to the requesting user's
   conversation (assistant turns only within it).
4. Fix `MemoryService.recall` dead graph branch (`results` → `matches`);
   replace hardcoded 0.6/0.5 scores with real normalized lexical scores;
   stop sorting on constants.
5. Persist `mode: "simulation"` on every deterministic multi-agent/workflow
   run record; simulated runs stop writing run-derived nodes into the KG.
6. Remove the hardcoded fusion-meter values in `hybrid-search.js` (render
   real scores from the API or remove the meters).
7. README honesty pass: agent/workflow claims rewritten to match reality
   (full rewrite happens in T10; this removes the falsehoods now).

Tests: authz regression tests (cross-workspace access denied), recall returns
graph results, context isolation test, run records carry mode.

### T2 — Packaging & app factory  *(unblocks clean work everywhere)*

Owns: root `setup.py`→`setup_wizard.py`, `pyproject.toml`, `requirements.txt`,
`latticeai/server_app.py` (assembly only), `latticeai/services/app_context.py`,
`latticeai/api/deps.py`, `latticeai/api/setup.py`, `latticeai/api/chat.py`
(telegram import only), `codex_telegram_bot.py`, `perm_monitor.py`,
`knowledge_graph_api.py`, `.github/workflows/*`, ruff config.

1. Rename root `setup.py` → `setup_wizard.py`; update importers
   (`server_app.py:149`, `api/setup.py`); add to `py-modules`; verify wheel
   contains it; add installed-wheel smoke test (build → clean venv → install
   → `import latticeai.server_app` → `/health`) to CI and scripts.
2. `create_app(config) -> FastAPI` factory: move import-time singleton
   construction and MLX init out of import scope; build `AppContext`; keep
   module-level names as thin accessors during deprecation (tests import
   them). Router factories accept the context (migrate the worst two:
   chat ~25 kwargs, workspace ~30 kwargs; others opportunistically).
3. Decouple telegram: `broadcast_web_chat` becomes a `RealtimeBus` subscriber
   registered only when `ENABLE_TELEGRAM`; `api/chat.py` drops the
   unconditional import.
4. Delete dead modules: `codex_telegram_bot.py`, `perm_monitor.py`,
   `knowledge_graph_api.py` (fold its one endpoint into
   `api/local_files.py`); remove from packaging lists.
5. ruff baseline (`[tool.ruff]`, pragmatic select set), fix violations or
   per-file-ignore legacy monoliths; CI gate. Bounded dependency constraints
   in pyproject; `requirements.txt` deleted or generated.

Tests: existing suite green; new wheel smoke test; factory produces a working
app (TestClient).

### T3 — Brain store: decomposition + v2 write-mastering + retrieval

Owns: `knowledge_graph.py` (becomes shim), new `latticeai/brain/` package
(`store.py`, `extraction.py`, `documents.py`, `discovery.py`), `kg_schema.py`
(moves to `latticeai/brain/schema.py` with root shim), `docs/kg-schema.md`.

1. Mechanical decomposition of `KnowledgeGraphStore` along the seams named in
   the backend audit; root `knowledge_graph.py` becomes a re-export shim.
   No behavior change; full suite green proves it.
2. v2 write-mastering flip per §3.1 migration strategy (backup → normalize →
   invert projection direction → equivalence tests). Canonical enums at every
   write site; populate owner/workspace/visibility/created_by/evidence.
3. Edge occurrence records (`observed_at`) instead of silent weight-max
   collapse; node `superseded_by`.
4. FTS5 index + query path replacing `LIKE` scans; `sqlite-vec` optional
   extra with capability-honest fallback; embedder provisioning stays
   consent-based via setup wizard.
5. Wire `graph_curator` promotion rules into ingestion (or delete the module
   — decide by measuring its tests against pipeline reality; no dead code
   survives).
6. Regenerate `docs/kg-schema.md` from the enums; delete claims about
   nonexistent APIs.

Tests: equivalence suite extended; migration idempotence; FTS5 parity tests;
scoped-write population tests.

### T4 — One door: ingestion 4/4 + durable conversations + garden absorption

Owns: `latticeai/services/ingestion.py`, `latticeai/services/upload_service.py`,
`latticeai/api/mcp.py`, `latticeai/server_app.py` (save_to_history call only),
new `latticeai/brain/conversations.py`, `p_reinforce.py`,
`latticeai/api/garden.py`.

1. New source types (`chat_message`, `upload`, `mcp`, `workspace_event`,
   `note`); rewire the three bypassing write paths through the pipeline;
   provenance coverage metric exposed (`/api/brain/provenance/coverage`).
2. `conversations`/`messages` SQLite store (unbounded, redaction on write);
   chat reads/writes it; `chat_history.json` retired to render-cache or
   removed; MemoryService conversation tier reads the store.
3. Garden absorption: one-time idempotent import of `~/.ltcai-brain` through
   the pipeline; `/garden` endpoints become brain queries; gardener's
   classifier becomes an ingestion enricher; chat context stops doing the
   O(n) vault rglob. Vault files left untouched on disk.

Tests: per-source provenance rows exist; conversation durability past 50;
garden import idempotence; chat context no longer reads the vault.

### T5 — Memory & Context systems

Owns: new `latticeai/brain/memory.py`, new `latticeai/brain/context.py`,
`latticeai/api/chat.py` (context build), `latticeai/api/memory.py`,
`latticeai/core/agent.py` (memory_update only),
`latticeai/services/search_service.py` (consumption only).

1. Memory model: Episodic/Semantic/Experience/Decision records as typed
   nodes (canonical enums from T3) with provenance; consolidation entry
   point (explicit, observable; no magic background jobs yet — the
   consolidation *runner* is a workflow under T7's triggers).
2. ContextAssembler: budgeted, ordered, provenance-carrying sections;
   hybrid search replaces LIKE context; workspace memories injected;
   per-section trace exposed to the UI ("why is this in context?").
3. Agent learnings flow through the ingestion pipeline as Experience/
   Decision records (no more vault markdown dumps with swallowed errors);
   real runs only.

Tests: assembler budget/order/provenance; memories actually retrieved at
chat time; learnings land as typed nodes.

### T6 — Personal/Org Brain: scoping, identity, transactional state

Owns: `latticeai/services/workspace_service.py`, `latticeai/api/search.py`,
graph read paths in `latticeai/brain/store.py` (scoping joins), new
`latticeai/core/policy.py`, `latticeai/api/admin.py`, `latticeai/api/auth.py`
(password policy/PKCE/session hashing), `latticeai/core/sessions.py`,
`latticeai/core/workspace_os.py` (storage backend), invitations API.

1. Scoped reads everywhere (search/graph/traverse/vector/context); NULL
   workspace = legacy-global compatibility, documented.
2. User UUIDs via non-destructive migration; sessions/memberships keyed on
   them; email mutable.
3. `core/policy.py` single role→capability map, enforced via router
   dependency; `/admin/roles` serves the now-true policy; invitation flow
   (create/accept/expire).
4. Workspace state → SQLite (same DB family), one-time JSON import, caps
   removed, per-operation transactions.
5. Auth hardening: session tokens hashed at rest, real password policy,
   PKCE on SSO exchange, delete dead `_sso_states`/`detect_edition` branch.

Tests: cross-workspace read denial on every read API; migration assigns
stable UUIDs idempotently; policy enforcement; truncation gone.

### T7 — Real Act runtimes

Owns: `latticeai/core/multi_agent.py`, `latticeai/services/platform_runtime.py`,
`latticeai/services/agent_runtime.py`, `latticeai/api/agents.py`,
`latticeai/api/workflow_designer.py`, `latticeai/core/agent_registry.py`,
new `latticeai/services/run_executor.py` (async engine),
`latticeai/core/workflow_engine.py` (trigger vocabulary),
new `latticeai/services/triggers.py`.

1. LLM-backed role runners (planner/critic via `llm_router` prompts;
   executor drives `core/agent.py`); `mode: "llm"` vs `mode: "simulation"`
   persisted; simulation never writes Experience records.
2. Workflow tool/skill nodes execute via `dispatch_tool`; `awaiting_approval`
   pause state; plugin capability runners execute or honestly refuse.
3. Async run engine: persisted run lifecycle, background workers, cooperative
   cancellation, SSE progress over `core/realtime.py`.
4. Per-tool approval gate generalizing `human_in_loop`; `approve()` stops
   auto-approving.
5. Triggers: interval/cron scheduler + brain-event subscriptions via hooks;
   trigger-fired runs carry event provenance.
6. Registry entries executable (model/prompt/tool allowlist consumed at run
   time); custom agents run for real.

Tests: a real LLM run path (mocked router), approval pause/resume,
cancellation, trigger firing creates runs, simulation labeling end-to-end.

### T8 — Sovereignty & Brain Network

Owns: new `latticeai/brain/identity.py`, `latticeai/services/kg_portability.py`,
new `latticeai/brain/network.py`, new `latticeai/api/network.py`,
`latticeai/api/portability.py`.

1. Device Ed25519 keypair (file + keyring); fingerprint surfaced in UI/API.
2. Signed bundles: detached signature + pubkey in export manifest; import
   verifies; per-workspace export for members (not admin-only).
3. Peer registry + pairing (manual pubkey exchange), push/pull signed
   bundles over HTTP (LAN/tailnet), origin-device provenance on imported
   nodes; idempotent content-hash dedup as v1 merge semantics.

Tests: sign/verify round-trip, tampered bundle rejected, unpaired peer
rejected, import provenance recorded.

### T9 — Brain UX  *(parallel-safe with T3-T8 after T1)*

Owns: `static/v3/**`, `static/*.html` + `static/scripts/**` (deletion),
`static/sw.js`, `latticeai/api/static_routes.py`, `latticeai/api/workspace.py`
(onboarding route), `knowledge_graph_api`-served `/graph` route relocation,
`scripts/lint_v3.mjs`, `scripts/build_v3_assets.mjs`, `STYLE_SYSTEM.md`.

1. IA regroup (Brain · Ask · Capture · Act · Library · System); Knowledge
   Graph = post-login landing view.
2. Port the force-directed canvas (zoom/pan/drag/physics) from legacy
   `graph.js` into the v3 explorer.
3. Legacy pages deleted; routes 308-redirect into `/app` equivalents;
   onboarding + admin land in `/app`; login rebuilt token-native.
4. Vendor Inter + icons locally; remove CDN references; rebuild `sw.js`
   around the v3 manifest.
5. Build artifacts ungitted (generated at release); lint_v3 extended (no raw
   hex outside token files; no inline style colors); i18n dictionary (en/ko).
6. Update Playwright/visual tests to the v3 surface; retire legacy-page
   suites.

Tests: Playwright smoke on /app views, redirect tests, zero CDN URLs in
shipped HTML/CSS/JS (lint rule), sw precache matches manifest.

### T10 — Release, identity, docs

Owns: version files, `scripts/bump_version.py` (new), `README.md`,
`PROJECT_PRINCIPLES.md`, `ARCHITECTURE.md`, `FEATURE_STATUS.md`,
`MODEL_POLICY.md`, `KNOWLEDGE_GRAPH.md`, `docs/EDITION_STRATEGY.md`,
`CHANGELOG.md`, `RELEASE_NOTES_v4.0.0.md`, `package.json` (files list),
`.gitignore`, `lattice_ai_full_spec.pptx` (delete at HEAD),
`MANIFEST.in`, release-note consolidation.

1. `bump_version.py` single-source bump; version → 4.0.0 everywhere;
   consistency test still guards.
2. Docs rewritten for the Digital Brain identity (constitution in
   PROJECT_PRINCIPLES); FEATURE_STATUS.md regenerated for v4 with the same
   honesty ledger discipline; MODEL_POLICY version fixed; release-notes
   sprawl consolidated (archive old, one current).
3. npm `files` slimmed; pptx deleted at HEAD; `.gitignore` covers
   tarballs/logs/venvs; `RELEASE.md` runbook separated from history.
4. Full validation: ruff, pytest, `scripts/validate_release_artifacts.py`,
   wheel smoke test, vsix build, npm pack dry-run size check.
5. Push branch; RC summary + 13-deliverable final report. STOP for human
   review (no merge, no tag, no publish).

## Execution model

Each track runs as its own workflow phase: implementer agent(s) with the
track's file-ownership list and this plan section as contract → reviewer
agent (correctness + "no fake functionality" + capability preservation) →
fix loop → full unit suite → commit. The recovery file is updated at each
track boundary.

## Risk register

- **v2 flip (T3)** is the highest-risk change: mitigations = automatic backup,
  idempotent migrator, equivalence suite, shim layer, and the flip lands as
  its own commit (revertable in isolation).
- **Legacy frontend deletion (T9)**: redirects + Playwright cover the user
  paths; deletion is one commit (revertable).
- **Async engine (T7)**: cooperative cancellation only (no thread kill);
  synchronous fallback path retained behind the same API contract.
- **Garden absorption (T4)**: vault is read-only source; gardener writer
  disabled only after import verifies; original files untouched.
- **Usage limits**: recovery file discipline; tracks commit independently so
  an interrupted track loses at most its own uncommitted work.
