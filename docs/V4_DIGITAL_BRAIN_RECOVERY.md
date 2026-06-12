# V4 Digital Brain — Transformation Program Recovery File

> **Purpose**: This file makes the v4.0.0 transformation program recoverable by any
> session (Claude, Codex, other models, or a human developer) without repeating
> completed analysis. **Update this file before ending any phase and before any
> likely session/context/usage limit.**
>
> Last updated: 2026-06-12 — T7c closed on main; T6/T9 gaps still active

---

## 0. RC STATUS (final)

**v4.0.0 release candidate is on `origin/feat/v4-digital-brain`.**
Validation: 571 unit tests pass · ruff clean · check:python 186 modules ·
lint_v3 all checks · installed-wheel smoke (19 modules from clean venv) ·
release artifacts validated (wheel + sdist + npm tgz, 2.0MB).
NO merge, NO tag, NO publish — awaiting review.
Remaining gaps (labeled in FEATURE_STATUS.md §v4.0.0 + RELEASE_NOTES_v4.0.0.md):
T6 remainder (UUIDs/policy/invitations/SQLite state), T9 remainder (legacy
deletion + parity views, login, i18n, T9b surfaces), pptx history rewrite
(owner), consent-gated embedder provisioning. All contracts live in
docs/V4_IMPLEMENTATION_PLAN.md.

## 1. Program Charter (from the user's v4.0.0 directive)

- Transform Lattice AI v3.6.0 into the **final-form Digital Brain Platform** (v4.0.0).
- Philosophy: models are temporary, knowledge is durable; user owns knowledge/memory/
  context; local-first, privacy-first, digital sovereignty.
- **Preserve capabilities** (may redesign, must not remove): local-first, Knowledge
  Graph (first-class, visible), graph visualization, search, model recommendation/
  installation, environment analysis, workflow/pipeline, multi-agent, personal +
  organization workspace, provenance, import/export, backup/restore.
- **Never fake functionality. No placeholders. No demo-only features.** If a
  capability can't be fully realized, build real architecture/interfaces/contracts.
- Git: work on `feat/v4-digital-brain` only; commit verified work frequently; push to
  remote feature branch; **no merge to main, no production release, no final tags** —
  prepare a release candidate and stop for review.
- Quality gates: lint, typecheck, tests, build, release-artifact validation, version
  refs updated, docs updated.
- Deliverables (13): product review, identity review, architecture review, UX review,
  data-model review, brain architecture proposal, implementation plan, implementation,
  validation results, risks/tradeoffs, remaining gaps, RC summary, commit history.

## 2. Current Phase

**Phase A (Repository Audit) — COMPLETE (all 8 dimensions).**
**Phase B (Brain Architecture Proposal + Implementation Plan) — COMPLETE.**
Adversarial design review done: 3 critics (feasibility, data-safety,
coherence), all `approve_with_changes`; 19 blocking issues integrated as the
NORMATIVE "Design-review amendments" section of
`docs/V4_IMPLEMENTATION_PLAN.md` + corrections in the architecture doc
(knowledge_graph_api.py is LIVE not dead; edges_v2 identity redefined;
chat-history import added; garden = watched source; T9b UI track added;
T7 owns workflow_engine.py + realtime.py with suspension/reconciliation
specs; tracks run strictly serially). Review record:
`docs/v4-audit/v4_design_review.json`.
**Phase C (Implementation) — T1 COMPLETE; next: T2 (Packaging & app factory).**

Track log (update at every track boundary):
- **T2 DONE** (commit `5e8aa1b`, 74 files). Agent did ~90% then died on a
  session limit; finished + verified inline. setup_wizard.py packaged & wheel
  smoke (scripts/wheel_smoke.py, runs in release CI, verified locally: 19
  modules import from clean-venv install); latticeai/app_factory.py
  create_app + lazy server_app facade (subprocess no-side-effect acceptance
  test in test_app_factory.py); AppContext chat+workspace routers; telegram
  via injectable on_chat_message; knowledge_graph_api → api/knowledge_graph
  (parity tests); llm_router → latticeai/models/router, mcp_registry →
  latticeai/core/mcp_registry (root shims); dead bots deleted; [tool.ruff]
  baseline — repo lints CLEAN, CI gate added; deps bounded;
  requirements.txt retired (CI+Dockerfile install from pyproject).
  Suite: 486 passed. Gotcha fixed inline: app_factory must keep the legacy
  alias imports (_agent_risk etc.) as locals — they ARE the server_app
  attribute surface via dict(locals()).
- **T3 IN PROGRESS as atomic sub-units**:
  - **T3a DONE** (commit `d7f8291`): FTS5 trigram index (node_fts + triggers
    + backfill), search() FTS-first w/ deterministic id-ASC ties, LIKE
    fallback (short queries + builds w/o trigram), capability reported in
    index_status().storage.fts_enabled. 7 tests; suite 493.
    Learned: search() reads the kgv2 views — direct legacy-table SQL is not
    a valid way to test read-path behavior; KnowledgeGraphStore ctor is
    (db_path, blob_dir); ingest_message(role, content, ...).
  - **T3b-1 DONE** (commit `650d4df`): edges_v2 rebuilt to
    UNIQUE(source,target,type,legacy_type) (create→copy→swap, re-entrant,
    data-preserving; projection ON CONFLICT updated); from_legacy()
    round-trips canonical values on both enums (was degrading
    CODE_FILE/AI_RESPONSE/… to CONCEPT). Suite 498.
  - **T3b-2 DONE** (commit `b7de8d7`): _upsert_edge normalizes ALL edge
    writes to canonical EdgeType (legacy label → metadata.legacy_label;
    synonyms dedupe; delete_conversation filters accept both vocabularies);
    nodes_v2 += workspace_id (additive ALTER heal), unscoped visibility =
    'legacy' sentinel, scope params threaded w/ COALESCE no-strip upserts.
    Suite 503. NOTE for T4/T6: ingestion + workspace callers should now
    pass owner/workspace_id explicitly (metadata user_email/workspace_id
    hints already resolve).
  - **T3c DONE** (commit after b7de8d7): edge_occurrences table (every
    observation recorded, cascades) + nodes_v2.superseded_by +
    mark_superseded(). Suite 507.
  - **T3e DONE**: docs/kg-schema.md rewritten to match code (false API
    claims removed; FTS5/scope/temporal documented).
  - **T3d DONE**: `knowledge_graph.py` is now a root compatibility shim;
    implementation moved under `latticeai/brain/` (`store`, `schema`,
    `projection`, `write_master`, `discovery`, `ingest`, `provenance`,
    `documents`, `retrieval`), with every module under 1,500 lines. v2 is
    the authoritative write door; legacy tables are maintained as the
    compatibility projection. Startup creates a one-time pre-flip SQLite
    backup for existing graph data, stamps `PRAGMA user_version=4`, refuses
    newer DB formats, and preserves legacy read/import compatibility.
    Focused KG validation: 43 passed.
- **T4.1 DONE** (commits `427d6a3` + `a2a1445`): chat (app_factory
  save_to_history), MCP (/mcp/call knowledge_graph_ingest), and uploads
  (upload_service) all route through IngestionPipeline — new
  CHAT_SOURCE_TYPES route calls ingest_message w/ role/conversation
  semantics; provenance_coverage() store metric + GET
  /knowledge-graph/provenance/coverage endpoint (parity baseline updated
  deliberately). Coverage now 4/5 (workspace events land with T6 rebuild).
  Suite 511.
- **T4.2 DONE** (commit `34ba891`): latticeai/brain/conversations.py
  ConversationStore (same SQLite file as the KG → backup co-location free);
  idempotent chat_history.json import; get_history/clear_* contracts
  preserved incl. legacy bucket + started_at sweep; MemoryService
  conversation tier reads the store. latticeai.brain packaged. Suite 517.
  Branch pushed to origin/feat/v4-digital-brain.
- **T4.3 DONE** (commit `17dbe0a`): gardener dual-writes (vault markdown
  mirror + pipeline ingest w/ provenance source_type=note); idempotent
  startup vault import; get_relevant_context = brain query (vault-scan
  fallback only when graph disabled); get_tree() implemented (was a
  latent 500). Suite 528.
- **T4.4 DONE** (commit `e341f74`): graph_curator live — store.curate()
  gated topic promotion w/ real nodes_v2.importance_score + POST
  /knowledge-graph/curate. Suite 530. Backup round-trip test proves
  conversations ride the KG backup. **T4 COMPLETE.**
- **T6.5 DONE** (commits `84ca636`+`443a8ce`): hashed session tokens at
  rest (transparent plaintext migration), 8+ alnum password policy on
  register/change-password, S256 PKCE on SSO. Suite 523→531.
- **T5 COMPLETE** (commits b12a68c, 4e8cd70, ca50d10): latticeai/brain/
  memory.py (BrainMemory: Decision/Experience typed records via new
  decision/experience/workspace_event pipeline source types; simulation
  runs REFUSED at the memory boundary) + latticeai/brain/context.py
  (ContextAssembler: budgeted chars/4 approx_tokens, per-section
  provenance, honest absence, seam isolation). Chat context = assembler
  (memories finally injected at inference + hybrid search replaces LIKE;
  doc-gen branch preserved; trace persisted as trace_seed.context_assembly).
  Agent learnings → Experience records via AgentDeps.brain_memory port
  (vault dump fallback only when port absent; no more bare-except).
  Suite 541.
- **T8 COMPLETE** (commit b1e05f4): latticeai/brain/identity.py (Ed25519
  device keypair, file 0600 default / keyring opt-in via
  LATTICEAI_DEVICE_KEY_KEYRING=1 — keyring at startup blew test runtime
  6s→237s, hence opt-in), signed export bundles + verified imports w/
  origin provenance + unsigned-legacy local policy, export(workspace_id)
  now REALLY filters (was header-only), latticeai/brain/network.py Brain
  Network v1 (pairing, signed+replay-protected peer auth, push/receive,
  signer-must-match-peer), /network API. Suite 548.
- **T7a DONE** (commit 1fc96ec): workflow tool nodes EXECUTE via
  dispatch_tool under governance; ApprovalRequired pauses runs into
  awaiting_approval w/ JSON cursor (WorkflowEngine.resume re-enters at the
  paused node, never re-executing; denial fails honestly); skill +
  plugin-skill nodes refuse honestly; plugin run_tool executes governed;
  live runs persist mode='live' + pause cursor; POST
  /workflows/api/runs/{id}/resume (one decision; record resolves).
- **T7b DONE** (commit 3064fa3): llm_role_runner — planner/executor/
  reviewer call the loaded model; parse failure FAILS the run w/ raw
  preserved (fail-closed); build_orchestrator picks mode='llm' only when
  a model is loaded; agents run endpoint via asyncio.to_thread + sync
  model bridge (asyncio.run safe in worker thread). Suite 559.
- **T6-scoped-reads DONE** (commit 7f58a57): workspaces_of/
  filter_scoped_nodes on the store; all SearchService channels + kg.graph()
  accept allowed_workspaces; search router scopes via _ScopedSearchService
  proxy + PLATFORM.allowed_scopes; ContextAssembler hybrid seam scoped per
  user. Legacy NULL rows machine-visible (documented). Suite 564.
- **T7d DONE** (commit 235f9b6): latticeai/services/triggers.py —
  interval scheduler (missed-while-down → recorded skip events, no
  catch-up) + brain_event triggers via visible post_tool hook on
  kg_ingest.*; __trigger__ provenance in run inputs; describe() honest
  status; started in app factory w/ idempotent hook registration. Suite 569.
- **T7e DONE** (commit 014ca91): custom registry agents executable
  (config actually loaded; honest skip in simulation).
- **T7c DONE**: `latticeai/services/run_executor.py` owns durable asyncio
  server-loop tasks for agent/workflow runs. `/agents/api/run` and workflow
  definition runs now persist queued rows, execute sync orchestrator/tool work
  via `asyncio.to_thread`, update the same run row through running/final states,
  publish progress through the existing realtime SSE feed, support cooperative
  cancellation, and reconcile orphaned active runs to `interrupted` at startup
  while preserving `awaiting_approval` pause cursors. `RealtimeBus.publish` is
  thread-safe via subscriber-loop `call_soon_threadsafe`. Suite: 579.
- **T9-canvas DONE** (commit 2fee077): force-directed canvas explorer
  (graph-canvas.js + Explore rewire; visual spec updated).
- **T9-IA DONE** (commit 972d34c): brain-first nav (Brain/Ask/Capture/
  Act/Library/System); knowledge-graph is the default landing route.
- **PHASE D DECISION**: moving to T10 RC prep. REMAINING GAPS (honest,
  labeled, not faked): T6 remainder (user UUIDs, enforced policy module,
  invitations, workspace SQLite state); T9 remainder (legacy page deletion +
  parity views, login rebuild, artifact ungitting, i18n, T9b
  approval/network/trace surfaces).
  All have full contracts in docs/V4_IMPLEMENTATION_PLAN.md + amendments.
- T9-canvas agent left static/v3/js/views/graph-canvas.js (509 lines,
  node --check passes) but NEVER rewired knowledge-graph.js — file kept
  uncommitted in tree; integration outstanding.
- NOTE: The old T3d queue is closed. T9 parity surfaces remain active with
  full contracts in this file + the plan.
  - **T3e**: docs/kg-schema.md regenerated from enums.
  - graph_curator decision moved to T4.
- **T9 PARTIAL — vendoring half DONE** (commit `aa613ae`, parallel-safe per
  amendments): all CDN references removed from every shipped page (Inter,
  Tabler icons, chart.js, marked.js vendored under static/vendor);
  --lt3-on-accent token added; sw.js rebuilt around the v3 manifest;
  lint_v3.mjs now mechanically enforces token/inline-style/CDN rules;
  6 guard tests. REMAINING T9: canvas graph port (item 2), IA regroup (1),
  legacy deletion + redirects (3, needs parity views), login rebuild,
  artifact ungitting, i18n, T9b surfaces (after T7/T8).
- **T1 DONE** (commits `1cddc67` frontend + `c574eb6` backend). All 7 items:
  by-id snapshot/memory authz via new WorkspaceService.authorize_record_read/
  authorize_memory_delete; /workspace/os leak removed (workspace_count
  replaces raw registry; legacy+v3 UI only read workspace_registry — checked);
  chat context pairing fix (pair_user_history, module-level in api/chat.py);
  recall matches-key fix + shared lexical scorer (no constants); run records
  carry mode/record_schema_version=2 and simulation runs skip KG ingestion
  (record_agent_run/record_workflow_run mode param; orchestrator declares
  mode; agent_runtime threads it); fake fusion meters removed (hashed bundle
  regenerated, manifest updated); README overclaims corrected.
  Suite: 469 passed (455 baseline + 3 static guards + 11 T1 tests).
  Note: T1 ran inline (main session) after the workflow implementer hit a
  usage limit; only the frontend half came from the workflow agent.

Scope ruling (user directive, session 3): risk/effort/migration size are NOT
valid exclusion reasons — only true technical blockers. Consequently the
architecture now INCLUDES: KG v2 write-mastering flip, durable async run
engine + cancellation + SSE + triggers, per-tool approval gate, user-UUID
identity + policy enforcement + invitations, transactional workspace state,
FTS5 + optional sqlite-vec, Brain Network v1 peer exchange (signed bundles
over LAN HTTP), knowledge_graph.py decomposition into latticeai/brain/,
root-module absorption, create_app factory, legacy frontend deletion,
token-native login, i18n. Only two exclusions remain (both true blockers):
git history rewrite for the tracked pptx (force-push = owner decision at RC
review; file IS deleted at HEAD in T10) and silent default download of a
production embedder (consent violation; wizard-provisioned opt-in instead).

Full structured audit findings for all 8 dimensions are committed at
`docs/v4-audit/v4_audit_<dimension>.json` (summary / strengths / problems
with severity+files / opportunities with effort). §4 below condenses the two
that predate the JSON drop; **read the JSON files for the other six — they are
the canonical Phase A record.**

## 3. Completed Work

1. **Baseline established (main @ 5889195, v3.6.0)**
   - Tests: `.venv/bin/python -m pytest tests/` → **455 unit pass, 9 integration
     fail**. The 9 failures are *pre-existing* `httpx.ConnectError`s — they need a
     live server. **Unit tests (`tests/unit`) are the validation gate.**
   - `.venv` Python is 3.14.5. `pyproject.toml` requires >=3.11 (avoid PEP 701
     f-strings nesting same quotes — 3.11 compat; CI runs 3.11).
   - Code inventory: `latticeai/` package ~15,007 lines (28 core modules, 16
     services, 27 API routers + `server_app.py` at 1,554 lines). Legacy root
     modules ~6,720 lines incl. `knowledge_graph.py` **4,633 lines**,
     `kg_schema.py` 521, `llm_router.py` 775, `mcp_registry.py` 791.
   - Frontend: `/app` v3 SPA (`static/v3/`, 22 views, token-native) is primary;
     legacy static HTML pages (`static/*.html`) still shipped in parallel.
   - Repo root clutter: ~30 `ltcai-*.tgz` tarballs, `ltcai-0.3.1/` extracted copy,
     logs, `chat_history.json`, 15MB pptx — most likely untracked; verify with
     `git ls-files` before cleaning.
2. **Branch created**: `feat/v4-digital-brain` (from main @ 5889195). No commits yet
   besides this recovery file.
3. **Phase A audits completed (2 of 8)** — full JSON in
   `/tmp/v4_audit_agent-workflow-runtime.json` and
   `/tmp/v4_audit_workspace-enterprise.json` (also summarized in §4 below; tmp files
   may not survive reboot — §4 is the durable record).

## 4. Findings (completed audit dimensions)

### 4.1 Agent & Workflow Runtime — VERDICT: one real runtime, two demo-grade ones

**Real (keep/extend):**
- `latticeai/core/agent.py` — genuine single-agent LLM state machine
  (PLAN→EXECUTE→VERIFY→ROLLBACK), real tool execution via `DEFAULT_TOOL_REGISTRY`
  (`tools/__init__.py:247-256`), destructive-action blocking, loop detection, git
  rollback, human-in-the-loop plan approval (`latticeai/api/chat.py:714-727`).
- Hooks platform is real as of v3.4+ (v3.3.0 gap closed): execution engine in
  `latticeai/core/hooks.py:498-713`, 7 built-ins bound at startup
  (`server_app.py:1327`), subprocess user hooks, fail-closed `pre_*` gates,
  persisted run log (`hooks_runs.json`), fired from agent/workflow/tool/ingestion.
- `dispatch_tool` (`hooks.py:187-233`) is the single shared tool lifecycle seam.
- `WorkflowEngine` (`core/workflow_engine.py`) is a clean, tested interpreter
  (validation, cycle guard, eval-free conditions) — the *engine* is fine.
- Tool governance single ownership point: `core/tool_registry.py`.

**Critical problems:**
- **Multi-Agent Runtime is deterministic theater**: production always uses
  `default_role_runner` (`platform_runtime.py:211-216`); planner emits canned
  3-step plan (`multi_agent.py:339-343`), self-approves, executor does no work,
  reviewer rubber-stamps — yet persists fake plans/handoffs/reviews into the
  workspace store **and the Knowledge Graph** (fabricated provenance).
- **Workflow runs execute nothing**: `platform_runtime._tool_node_runner` (:79-97)
  returns `{recorded: true}` instead of calling `execute_tool`; skill/plugin
  runners are existence checks. Runs finish "ok" having done zero work.
- Custom agents in `AgentRegistry` are metadata-only — orchestrator filters to 5
  hardcoded `AGENT_ROLES` (`multi_agent.py:476`); registration is a UI illusion.
- No async execution/cancellation/scheduling; `stop()` can't cancel; only
  'manual' trigger exists.
- Tool approval is audit-only (`agent.py:176-194` always auto-approves);
  per-tool human gate doesn't exist despite governance vocabulary.
- Two parallel agent systems with colliding names (`core/agent.py` vs
  `core/multi_agent.py`+`services/agent_runtime.py`).

**Key opportunities (= v4 work):** back orchestrator roles with the real
single-agent runtime + LLM router; make workflow tool nodes call `dispatch_tool`
with real governance (pause-for-approval state); async durable runs + SSE events +
real cancellation; trigger system (cron/interval + KG-event triggers via existing
hooks, e.g. "on document ingested, run workflow"); unify agent systems so registry
entries carry executable config (model/prompt/tool allowlist); route agent
learnings through `services/ingestion.py` with provenance; label simulation runs
honestly (`mode` field) until/unless execution is real.

### 4.2 Workspace, Identity & Enterprise — VERDICT: solid auth, illusory isolation

**Real (keep):** `core/oidc.py` (fail-closed OIDC verifier, anti-downgrade),
SSO nonce binding (`api/auth.py:137-201`), honest open-core enterprise seam
(`core/enterprise.py` — everything reports `enabled=False`), `core/security.py`
(scrypt, trusted-proxy XFF, constant-time compares), `PermissionGateway`
(path+action+user+hash+TTL consent), workspace role enforcement in store with
tests, non-destructive workspace migration.

**Critical problems:**
- **The actual "brain" is machine-global, not workspace-scoped**:
  `workspace_service.py:39` `SHARED_GLOBAL_AREAS = ('graph', 'skills')`;
  KG store constructed once per machine (`server_app.py:296+`); chat history
  global; portability export is admin-only machine-global. Personal vs
  Organization workspace isolation only covers auxiliary JSON records.
- **By-id authz bypasses**: `GET /workspace/snapshots/{id}` (+`/{area}`,
  `/export`, `/compare`) only `require_user` — any authenticated user reads any
  workspace's snapshots (`workspace.py:343-389`). Memory delete lacks ownership
  checks; `/workspace/os` leaks full registry incl. other orgs' member lists
  (`workspace_os.py:433`).
- Single unlocked whole-file `workspace_os.json` (1,959 lines module, 0 locks):
  lost updates under concurrency; silent `[-200:]`/`[-500:]` truncation of
  memories/traces/timeline — contradicts "knowledge is durable".
- Three conflicting role vocabularies (users.json admin|user; workspace
  owner/admin/member/viewer; `_ROLE_CAPS` matrix that **nothing enforces** though
  `admin.py:112-113` claims it's "the real access policy").
- Minor: session tokens stored plaintext; 4-char min password; dead
  `detect_edition()` env branch; dead `_sso_states`; org-creation timeline event
  mis-scoped; SSO lacks PKCE.

**Key opportunities (= v4 work):** partition KG by workspace (prereq for
Personal/Organization Brain) — `~/.ltcai/workspaces/<id>/` or workspace_id
columns, threaded through ingestion/search/portability; close by-id authz gaps
(small!); unify identity (stable user UUIDs, one policy module, real
invitations); per-workspace SQLite for workspace state (kill lost updates +
truncation); federation foundations: device keypair identity (keyring), signed
provenance-stamped export bundles, selective sharing; visibility levels
(private/workspace/org) on memories+nodes; per-user "take your brain with you"
export + encryption at rest; harden edges (hash session tokens, PKCE, password
policy).

### 4.3 Remaining six dimensions — COMPLETE; headline findings

Canonical record: `docs/v4-audit/*.json`. Cross-dimension headline synthesis:

- **product-identity**: identity is skin-deep — only README/ARCHITECTURE say
  "Digital Brain"; PROJECT_PRINCIPLES/pyproject/package.json/SPA IA still say
  "AI workspace". **p_reinforce.py "garden" vault (`~/.ltcai-brain`) is a second
  brain bypassing the KG**, injected into every chat (`api/chat.py:368`),
  contradicting "no source bypasses the graph". README overclaims agents/
  workflows that FEATURE_STATUS admits are LLM-free. Naming sprawl (9 ids,
  2 env prefixes, uppercase `LTCAI` bin). FEATURE_STATUS.md honesty ledger is
  the prize asset — institutionalize it.
- **backend-architecture**: inverted dependency — clean `latticeai/` imports
  legacy root modules everywhere. `knowledge_graph.py` = 4,633-line single
  class w/ 7 responsibilities. **IngestionPipeline covers only 1 of 4 KG write
  paths** (browser only; chat/uploads/MCP write directly, no provenance).
  `server_app.py` 1,555-line god module, import-time side effects, dormant
  AppContext/deps.py. Chat history hard-capped at 50 messages in JSON.
  telegram_bot imported unconditionally by chat router. Dead: codex_telegram_bot,
  perm_monitor, knowledge_graph_api (vestigial).
- **knowledge-data-model**: KG v2 is **schema theater** — reads reconstruct
  legacy Korean free-string types via COALESCE views; v2's owner_id/visibility/
  evidence/created_by/embedding columns never populated; writes still mint
  '업로드함'/'포함함'. No temporal/episodic dimension (edges UNIQUE collapse
  history). No memory-type model. Search default = LIKE + brute-force cosine
  over hash embeddings (grade='fallback'). graph_curator.py dead in production.
  docs/kg-schema.md documents nonexistent APIs (validate_endpoints).
- **frontend-ux**: TWO complete frontends in production (legacy ~17k lines at
  /chat,/graph,/workspace,/admin… vs v3 SPA at /app); onboarding + /admin route
  into the LEGACY stack. v3 KG explorer (static SVG) is weaker than legacy
  force-directed canvas — backwards for KG-first. CDN fonts/icons contradict
  privacy-first. sw.js stale (precaches legacy). Hashed build artifacts
  committed beside sources. lint_v3.mjs is syntax-check only.
- **memory-context**: memory IS injected at chat time but naively (string
  concat of vault substring-scan + SQLite LIKE); workspace personal-memory tier
  NEVER consumed at inference; **`MemoryService.recall` graph branch dead code**
  (`.get("results")` vs actual `matches` key); fabricated recall scores
  (hardcoded 0.6/0.5); recent-chat context **leaks other users' messages**
  (filter passes any assistant reply); hybrid/vector search never used at
  inference; agent learnings dumped to vault markdown w/ swallowed errors.
- **release-quality**: **published wheel is broken** — `server_app.py:149`
  imports root `setup` module which py-modules omits; root `setup.py` is
  application code colliding with setuptools. Zero Python lint/typecheck.
  Deps fully unpinned (pyproject + duplicated requirements.txt). npm tarball
  24.8MB (ships docs images, bots). Version = 9 synchronized copies guarded by
  a test. 15MB pptx tracked at HEAD. Root clutter (31 tgz, 2 venvs, logs) is
  untracked (440 tracked files; 0 tgz tracked). Strong assets to keep:
  validate_release_artifacts.py, version-consistency tests, CI matrix,
  tag-driven release workflow.

## 5. Decisions Made

1. `feat/v4-digital-brain` is the working branch; main untouched.
2. Unit tests (455) are the green gate; the 9 integration failures are
   pre-existing and excluded from the gate (re-verify they don't regress further).
3. Phase structure: A audit → B design (Brain Architecture Proposal + impl plan,
   with adversarial design review) → C implementation tracks (disjoint file
   ownership, frequent verified commits) → D validation + RC + final report.
4. Audit failures are re-run as a fresh 6-dimension workflow (not resume) to
   avoid cache ambiguity around failed agents.
5. Recovery discipline: update this file at every phase boundary and before
   any foreseeable limit.

## 6. Remaining Work / Exact Next Actions

1. **[NOW] Re-run the 6 failed audit dimensions** (same prompts as in workflow
   script `v4-audit-wf_d690b8d1-60c.js` under the session workflows/scripts dir;
   prompts are reproducible from §4.3 dimension list + FINDINGS schema).
2. Merge all 8 findings into §4 of this file; mark Phase A complete.
3. **Phase B**: write `docs/V4_BRAIN_ARCHITECTURE.md` (Brain Architecture
   Proposal) + `docs/V4_IMPLEMENTATION_PLAN.md`; run adversarial design review
   (2-3 critic agents); revise; commit.
4. **Phase C**: implement per the plan (queue below), committing after each
   verified track.
5. **Phase D**: full validation, version bump to 4.0.0 (RC), docs, release notes,
   push branch, final 13-deliverable report. STOP — wait for human review.

## 7. Detailed Implementation Queue

**SUPERSEDED by `docs/V4_IMPLEMENTATION_PLAN.md` (tracks T1–T10 with file
ownership, migration strategies, tests, and risk register). That document is
the execution contract for Phase C.** The original provisional queue below is
retained for context only:

- **C1. Truth & safety floor (small, do first)**
  - Close workspace by-id authz gaps; strip registry leak from `/workspace/os`.
  - Mark multi-agent/workflow simulation runs with persisted `mode:
    "simulation"`; stop writing fabricated runs into the KG as real provenance.
  - Hash session tokens at rest; real password policy; PKCE on SSO exchange.
- **C2. Brain Core data layer**
  - Workspace-partitioned Knowledge Graph + memory + chat scoping
    (Personal Brain vs Organization Brain become real).
  - Durable workspace state (per-workspace SQLite or locked store); remove
    silent truncation.
  - Memory model: episodic/semantic/experience/decision record types with
    provenance, on the KG substrate.
- **C3. Real Agent Runtime**
  - LLM-backed role runners on top of `core/agent.py` + `llm_router`;
    registry entries become executable (model/prompt/tool allowlist).
  - Per-tool approval gate generalizing the human-in-loop pause.
- **C4. Real Workflow Runtime**
  - Tool/skill nodes execute through `dispatch_tool` under governance with
    pause-for-approval; async runs + cancellation + SSE progress.
  - Trigger foundations: interval/cron + KG-event triggers via hooks.
- **C5. Sovereignty & federation foundations**
  - Per-user/per-workspace brain export (signed bundles, device keypair),
    import with provenance; visibility levels.
- **C6. Identity unification** — user UUIDs, single policy module, invitations.
- **C7. UX/IA re-architecture** — pending frontend-ux audit results.
- **C8. Backend decomposition** — knowledge_graph.py monolith etc., pending
  backend audit results.
- **C9. Release hygiene** — version single-source, root cleanup, lint/typecheck
  story, pending release-quality audit results.

## 8. Planned Phase B Activities

- Synthesize all 8 audits into: Product Review, Identity Review, Architecture
  Review, UX Review, Data Model Review (deliverables 1-5).
- Author **Brain Architecture Proposal**: Brain Core; Memory/Knowledge/
  Relationship/Experience/Decision/Context systems; Agent Runtime; Dynamic
  Workflow Runtime; Personal Brain / Organization Brain / Brain Network /
  Knowledge Exchange / Federation foundations — mapped onto the real existing
  seams (ingestion pipeline, hooks, dispatch_tool, workspace service, KG store).
- Author Implementation Plan with track ownership (disjoint files per track).
- Adversarial review: 2-3 critic agents attack the proposal (feasibility,
  fake-functionality risk, capability-preservation, migration safety); revise.
- Commit both docs.

## 9. Planned Phase C Activities

- Execute queue §7 as sequenced tracks; after each track: run
  `.venv/bin/python -m pytest tests/unit -q` (+ targeted new tests; every new
  feature ships with tests), commit with conventional message, update this file.
- Implementation agents must follow: no placeholder code, no demo data, honest
  labeling, additive migrations with backfill, 3.11-compatible syntax.

## 10. Planned Phase D Activities

- `scripts/validate_release_artifacts.py`, `scripts/lint_v3.mjs`, full pytest,
  `npm`/vsix build as applicable, packaging build.
- Version → 4.0.0 across pyproject.toml/package.json/setup.py/health endpoint
  (verify the single-source mechanism from v3.3.0 audit).
- Update README/ARCHITECTURE/FEATURE_STATUS/CHANGELOG + RELEASE_NOTES_v4.0.0.md.
- Push `feat/v4-digital-brain`; produce final 13-deliverable report; STOP for
  human review (no merge, no tag, no publish).

## 11. Branch Status

- `feat/v4-digital-brain` exists locally, based on main @ 5889195 (v3.6.0).
- Not yet pushed to origin. No implementation commits yet.

## 12. Validation Status

- main baseline: 455 unit pass / 9 pre-existing integration failures
  (ConnectError, need live server). Nothing run on the branch yet beyond this.

## 13. Files Modified (branch vs main)

- `docs/V4_DIGITAL_BRAIN_RECOVERY.md` (this file) — NEW.
- (none else yet)
