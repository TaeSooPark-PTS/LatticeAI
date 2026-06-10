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

Tracks are sequenced by dependency and run **strictly serially** — several
files have multiple owners across tracks (`api/chat.py`: T1/T2/T5;
`server_app.py`: T2/T4/T6; `api/workspace.py`: T1/T9; `brain/store.py`:
T3/T6/T8), so sequence, not disjointness, is the conflict protection.
"Owns" below means: *during that track*, only that track touches the file,
and only within the named scope. T1/T2 unblock everything; T3→T6 are the
brain spine; T7/T8 build on the spine; T9 items 2-5 (canvas, vendoring,
sw.js, build/lint) may run in parallel with backend tracks, but T9 items
1/3/6 and all of T9b depend on the T3-T8 API contracts. T10 closes.

> **NORMATIVE**: every track below is amended by §"Design-review amendments"
> at the end of this document. Implementers must read both the track section
> and its amendments — the amendments win on conflict.

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
4. Delete dead modules: `codex_telegram_bot.py`, `perm_monitor.py`; remove
   from packaging lists. **`knowledge_graph_api.py` is NOT dead** — it serves
   the `/knowledge-graph/*` data endpoints the v3 SPA consumes: migrate its
   data router into `latticeai/api/knowledge_graph.py` with endpoint-parity
   tests (stats/graph/documents/search/context/neighbors/ingest unchanged);
   its legacy `/graph` page routes move to T9's redirect map. Also owned
   here: relocate `llm_router.py` → `latticeai/models/router.py` and
   `mcp_registry.py` → `latticeai/core/mcp_registry.py` with root shims, and
   update their importers (`api/models.py`, `api/setup.py`,
   `services/model_runtime.py`, `server_app.py`).
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

## Design-review amendments (NORMATIVE — bind all tracks)

Adversarial review verdicts: 3× approve_with_changes
(`docs/v4-audit/v4_design_review.json`). Required changes, by track:

**T1**
- Run-record changes are versioned: add `record_schema_version` alongside
  `mode`; simulated runs stop stamping `graph_node_id`. Rewrite the affected
  legacy tests deliberately (`test_agent_platform_maturity`,
  `test_v32_platform`, `test_multi_agent`, `test_workspace_os`) — do not
  discover them broken mid-track.
- The hybrid-search.js fix must reach the shipped bundle: T1 is granted a
  one-off `scripts/build_v3_assets.mjs` run to regenerate the hashed
  artifact + manifest.

**T2**
- `create_app` acceptance is NOT "TestClient works": the test must assert
  that importing `latticeai.server_app` (or the new factory module) performs
  no side effects — no MLX/GPU init, no singleton construction, no file
  creation under a sandboxed `LATTICEAI_HOME`. A delegating wrapper around
  the old import-time module fails this gate by construction.
- Wizard-driven embedder provisioning (consent flow) is owned by T2's
  `setup_wizard.py`/`api/setup.py` scope: expose a real provision endpoint
  with explicit user consent, honest progress, and capability re-report.

**T3**
- **Edge identity**: post-flip canonical edges key on
  `UNIQUE(source, target, type)`; migrated legacy rows keep their
  `legacy_type` discriminator. SQLite migration = create-new → copy → swap
  in one transaction, under the automatic pre-flip backup. Test: two
  canonical-typed edges (e.g. MENTIONS + CONTAINS) between the same node
  pair coexist.
- **Equivalence contract**: byte-equivalence is asserted for pre-flip data
  only; new canonical writes get a separate projection-correctness suite
  (English enum strings on the legacy surface are correct there, not a bug).
- **Downgrade guard**: set a DB format marker (`PRAGMA user_version` or
  `kg_meta` key) at flip time; v4 refuses to open a newer-format DB than it
  understands; document that v3.6 must not be pointed at a flipped DB and
  provide the restore runbook for the automatic pre-flip backup. The
  migrator is re-entrant, keyed on inspected data state, not a one-time
  stamp. (Same downgrade-guard pattern applies to T4/T6 stores.)
- **Migrated-row scope**: legacy rows get `visibility=NULL` semantics
  (legacy-global) — the `DEFAULT 'private'` column default must not be
  allowed to privatize pre-v4 shared data to its last writer.
- **Store write API**: enum normalization is enforced *inside*
  `brain/store.py` write methods (no caller can mint free strings);
  owner/workspace/visibility are parameters defaulting to legacy-global
  NULL — T4 (ingestion) and T6 (scope resolution) progressively supply real
  values; a post-T6 acceptance check reports the % of new writes carrying
  scope via the provenance coverage endpoint.
- **Decomposition definition of done**: the split follows the class's real
  method clusters — `store.py` (storage + v2 projection), `discovery.py`
  (local roots/audit/watch), `ingest.py` (ingest paths), `provenance.py`,
  `documents.py`, `extraction.py`, plus portability seam; no resulting
  module exceeds ~1,500 lines; a pure mixin-shuffle that recreates the god
  object across files fails review. `local_knowledge_api.py` disposition is
  owned here too (absorb into `brain/discovery.py` + API shim).
- **FTS5**: gate on `sqlite3` FTS5 availability with the same
  capability-honest fallback as sqlite-vec (the LIKE path survives as
  fallback); use the trigram tokenizer where available so Korean substring
  recall does not regress — add a Korean-recall regression test
  ('프로젝트' must match '프로젝트를').

**T4**
- **Chat history is imported, not dropped**: one-time idempotent import of
  `chat_history.json` into the conversations store; messages lacking
  user/conversation attribution land in a designated `legacy` conversation;
  the `/history` API response contract is preserved (grant: `get_history`,
  ChatService wiring in `server_app.py`, `/history` endpoints). Durability
  test: pre-upgrade messages visible post-cutover.
- **Garden**: continuous ingestion via the watched-source machinery (see
  architecture §4.2), not a one-time import; API-created notes dual-write
  (brain authoritative, vault markdown mirror); imported vault notes are
  legacy-global scoped. The `graph_curator` wire-or-delete decision moves
  here (it gates concept promotion at ingest time).
- **Store co-location**: conversations live in the brain DB family covered
  by `kg_portability` backup/restore — extend the backup manifest + restore
  path to enumerate them, with a restore round-trip test.
- The "chat context stops reading the vault" change in `api/chat.py:368`
  belongs to **T5** (context assembly), not T4.

**T5**
- Token budgeting uses a documented approximation (chars/4) — named as such
  in code and API responses (`approx_tokens`), never presented as a real
  tokenizer count.

**T6**
- **Identity migration scope**: one migration rewrites email→UUID keys
  across `users.json`, workspace state, sessions, AND
  `nodes_v2`/`edges_v2` owner/created_by values (T3/T4 write emails until
  then — the migration maps them). Atomic tmp+rename writes; timestamped
  pre-migration copies of `users.json` and `workspace_os.json`; explicit
  grant over `server_app.py`'s user-store functions (move them into a
  T6-owned module first). Downgrade is a one-way door — say so in the
  migration marker + docs, same pattern as T3.
- Invitations API lives in new `latticeai/api/invitations.py`.
- New workspace-state tables join the same backed-up DB family as T4
  (one backup covers the whole brain).

**T7**
- Ownership expands to **full** `workflow_engine.py` and `core/realtime.py`
  (thread-safe publish via `loop.call_soon_threadsafe`).
- **Suspension model**: the engine returns/raises a `PausedRun` carrying the
  node cursor + JSON-serializable context snapshot; runner exceptions are
  partitioned (`ApprovalRequired` → pause; others → error-and-continue as
  today); resume re-enters at the paused node and **never re-executes
  completed nodes** (explicit test required).
- **Execution model**: asyncio tasks on the server loop; sync orchestrator/
  tool work via `asyncio.to_thread`; SSE over `/realtime/stream`;
  the honesty boundaries (MLX generate non-interruptible; single inference
  thread serializes agent + chat) are documented and surfaced.
- **Startup reconciliation**: non-terminal runs → `interrupted` (reason +
  timestamp) before workers start; restart test required.
- **Missed-trigger policy**: missed interval/cron firings while down are
  skipped with a recorded skip event (no silent gaps, no thundering
  catch-up).
- **LLM-output failure policy**: when a model responds but the plan/critique
  cannot be parsed, the run FAILS with the raw output preserved in the run
  record — it never silently falls back to fabricated deterministic
  artifacts. Choosing simulation mode is explicit (no model loaded or
  user-requested), never a parse-failure disguise.

**T8**
- Unsigned legacy bundles/backups import fine locally with
  `origin='unsigned-legacy'` provenance; signatures mandatory only on the
  peer path. Test: a v3.6.0-format export imports; a pre-v4 backup restores.
- Peer-request auth: Ed25519 signature over (body digest + timestamp) against
  the paired key; freshness window + seen-nonce replay protection.
- Grant: the store's `export_graph_data`/`import_graph_data` functions for
  scope-filtered export + provenance-stamped import.

**T9 / T9b (new)**
- **Capability-complete deletion rule**: a legacy page is deleted only when
  its capabilities exist in `/app` and pass Playwright coverage — the
  redirect map must be capability-complete, not URL-complete. Gap views that
  must be BUILT first: workspace/org management (orgs, members, invitations,
  activation), snapshots/time-machine (list/create/compare/restore),
  activity feed, account profile. Chat parity explicitly includes doc-gen
  sessions, image attach, and file-path injection rendering.
- **T9b (sequenced after T7/T8)** — surfaces for the new APIs, with
  Playwright coverage: Act runs inbox (live progress, cancel, mode badge,
  approval pause→decide→resume), trigger configuration, System network view
  (device fingerprint, peer registry, pairing), Ask context-trace panel
  ("why is this in context"), Brain provenance-coverage stat. Until T9b
  lands, these capabilities are explicitly labeled API-only in
  FEATURE_STATUS.md — a labeled state, not an omission.
- **i18n acceptance gate**: all strings in `routes.js`, the shared shell,
  and every NEW v4 view are externalized; a checker script fails the build
  on string literals in those files; remaining legacy-view strings are
  inventoried in FEATURE_STATUS.md as labeled partial coverage.

**T10**
- Env-prefix canonicalization (`LATTICEAI_*` canonical, `LATTICE_*` read as
  fallback aliases in `core/config.py`) and the CLI alias decision
  (`ltcai` canonical, `LTCAI` deprecated) are owned here.
- Delete the superseded C-queue from the recovery file (replaced by this
  plan) to remove contradictory guidance.
- Pre-flip migration backups: note in the restore runbook that backups live
  on the same disk (exports dir) — recommend the user copy one off-disk at
  upgrade time; the upgrade flow prints the backup path.

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
