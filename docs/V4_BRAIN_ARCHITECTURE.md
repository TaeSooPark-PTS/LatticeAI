# Lattice AI v4.0.0 — Brain Architecture Proposal

Status: Phase B proposal (post 8-dimension audit, pre-implementation review)
Audit evidence: `docs/v4-audit/*.json` · Program state: `docs/V4_DIGITAL_BRAIN_RECOVERY.md`

---

## 1. The one-sentence architecture

**There is exactly one brain: a workspace-scoped, provenance-stamped knowledge
substrate in SQLite, with a single write door (the Ingestion Pipeline), a single
read discipline (scoped, scored, honest retrieval), and replaceable everything
else (models, agents, UI).**

v3.6.0 already *claims* this ("every source converges into the graph — no
silos"). The audit proves the claim false today in four ways:

1. A second brain exists (`~/.ltcai-brain` markdown vault, `p_reinforce.py`)
   with its own retrieval path injected into every chat.
2. The Ingestion Pipeline covers 1 of 4 write paths (browser only); chat,
   uploads, and MCP write to the graph directly, without provenance.
3. Conversations — the richest episodic source — are capped at 50 messages in a
   JSON file and then destroyed.
4. The graph is machine-global: workspace isolation excludes the actual brain
   (`SHARED_GLOBAL_AREAS = ('graph', 'skills')`).

v4 makes the claim true. That is the release.

## 2. Identity

- **Product**: *Lattice — the local-first Digital Brain Platform.* The phrase
  "AI workspace" is retired from every shipped artifact (PROJECT_PRINCIPLES,
  pyproject, package.json, vscode-extension, EDITION_STRATEGY, SPA copy).
- **Honesty is the brand.** FEATURE_STATUS.md's WORKING/PARTIAL/PLACEHOLDER
  ledger is institutionalized: README claims must trace to ledger entries; runs
  that simulate must say so *in the persisted record*, not just in docs.
- **Naming**: brand "Lattice AI"; packages stay `ltcai` (renaming published
  pip/npm packages is churn without user value). CLI: `ltcai` is canonical,
  `LTCAI` retained as deprecated alias. Env: `LATTICEAI_*` canonical;
  existing `LATTICE_*` vars read as fallback aliases. Data home stays `~/.ltcai`
  (a data migration of every user's brain for a nicer folder name violates
  "knowledge is durable"). `~/.ltcai-brain` is absorbed and retired (§4.2).
- Concept renames in UI/docs: "Workspace OS" → internal term only; the user
  sees **Brain** (graph+memory), **Capture** (ingestion), **Ask** (chat),
  **Act** (agents/workflows/tools), **Library** (models/skills/plugins),
  **System** (settings/admin).

## 3. The Brain Core model

All durable state converges on the existing SQLite graph store, extended — not
replaced (additive migration, the v3 store keeps working).

### 3.1 Knowledge System — v2 becomes the authoritative store
The half-finished strangler-fig migration is **finished in v4**, not deferred:

- **Write-mastering flips to the normalized v2 schema.** All write paths write
  canonical `kg_schema.NodeType`/`EdgeType` enums natively (no new Korean
  free-string types are ever minted again) and populate the columns that
  justify v2's existence: `owner_id`, `workspace_id` (new), `visibility`,
  `created_by`, `evidence`, confidence.
- **Migration strategy** (additive, reversible, backup-first):
  1. automatic pre-migration binary backup via the existing
     `kg_portability` backup machinery;
  2. one-time migrator normalizes legacy rows through the existing
     `from_legacy()` mapping (the same logic the projection uses today),
     preserving original strings in `legacy_type` — nothing is lost;
  3. legacy `nodes`/`edges` tables become a *read-compatibility projection*
     (the exact inverse of today), regenerated on write for one deprecation
     release, then dropped;
  4. the existing `test_kg_v2_read_equivalence.py` suite is extended to prove
     byte-equivalent reads before and after the flip.
- **Retrieval upgrade**: an FTS5 index over titles/summaries/chunk text
  replaces `LIKE '%q%'` scans; `sqlite-vec` is integrated as an optional
  extra (`ltcai[ann]`) with the brute-force cosine path remaining as the
  honest, capability-reported fallback. The default embedder stays honestly
  labeled `grade='fallback'`; a *real* local embedding model is provisioned
  through the setup wizard **with explicit user consent** (a silent
  multi-hundred-MB download at install would violate privacy-first — this is
  the only "default" we refuse on principle, not effort).
- **Temporal dimension**: edges gain `observed_at` occurrence records so
  repeated observations no longer collapse silently (`weight=max` losing
  history); nodes gain `superseded_by` for revision chains.

### 3.2 Memory System (new, on the same substrate)
Four first-class record kinds, stored as typed nodes with provenance:
- **Episodic** — immutable, timestamped: conversation turns, tool runs,
  ingestion events. Source: the new durable conversation store + agent runs.
- **Semantic** — consolidated facts/preferences/working-style. Today's
  `MEMORY_KINDS` workspace memories become readable at inference (they are
  written today but never read by the model — audit: memory-context critical).
- **Experience** — completed agent/workflow runs promoted into the graph
  (plan, outcome, retries) — only *real* runs; simulations are labeled and
  never enter the brain as experience.
- **Decision** — explicit decision records (already a node type in the schema,
  never populated; v4 populates it from agent plan approvals and user-saved
  decisions).

### 3.3 Conversation store (new; kills the 50-message cap)
`conversations`/`messages` tables in the brain DB family: unbounded, per-user,
per-conversation, redaction applied on write, source of the chat UI and of
episodic memory. `chat_history.json` becomes a render cache at most.

### 3.4 Context System (new: the ContextAssembler)
One pipeline replaces the ad-hoc string concatenation in `api/chat.py:365-418`:
ordered, token-budgeted sections (system → semantic memories → hybrid-search
knowledge → episodic recency → attachments), each with provenance (`why is
this in my context?` is answerable). It uses the *existing, tested*
`SearchService.hybrid_search` — fixing the absurdity that the product's search
engine is never used by its own chat. The recall scoring bug
(`results` vs `matches`) and the fabricated constant scores die here.

### 3.5 Relationship System (exists)
The graph's edges, plus evidence/confidence threading from the extractors
(already produced, currently discarded in projection).

## 4. One door in: the Ingestion Pipeline

### 4.1 Coverage goes 1 of 5 → 5 of 5
`services/ingestion.py` becomes the only KG write door: alongside the
already-covered browser/web path, chat messages, document uploads, MCP
messages, and workspace events are converted to `IngestionItem`s (new source
types), giving every node provenance and the full pre/post hook lifecycle.
The direct `ingest_message`/`ingest_document` call sites are rewired.

### 4.2 The garden is absorbed — as a living source, not a snapshot
`p_reinforce` vault content enters the brain through the pipeline
(source_type=note): an initial idempotent import (content-hash dedup), after
which the vault directory is registered as a **watched knowledge source**
using the existing discovery/watch machinery — Obsidian-style edits keep
flowing into the brain continuously. Notes created through the API are
written to the brain (authoritative) *and* mirrored as markdown into the
vault, which remains the user-readable, user-owned artifact. The `/garden`
API and chat-context injection are re-implemented as views/queries over the
brain; the O(n) vault rglob at chat time dies. Imported vault notes carry
legacy-global scope (NULL workspace), matching their pre-v4 visibility.
**No capability is removed** — notes, classification, Obsidian
interoperability, and "relevant context in chat" all survive with strictly
better retrieval.

## 5. Personal Brain / Organization Brain

The workspace layer stops being a veneer:
- Graph writes carry `workspace_id` + `owner_id` (from the already-resolved
  `WorkspaceService` scope); reads (`search`, `graph`, `traverse`,
  `vector_search`, context assembly) filter by resolved scope. Personal brains
  are private by construction; organization workspaces share an org brain.
  Pre-v4 rows have no workspace (NULL = legacy-global, readable by all members
  of the machine as today — honest, documented compatibility).
- The by-id authorization bypasses (snapshots get/area/export/compare, memory
  delete, `/workspace/os` registry leak) are closed.
- Chat context no longer leaks other users' messages (the
  `role=="assistant"` filter bug).
- **Identity is unified**: every user gets a stable UUID (assigned by
  non-destructive migration on first load, email becomes a mutable
  attribute); memberships, memories, sessions, and audit entries key on it.
  The three role vocabularies collapse into one policy module
  (`latticeai/core/policy.py`) that defines role→capability mappings and is
  *actually enforced* at the router layer — retiring the false
  "`_ROLE_CAPS` is the real access policy" claim by making it true.
  Organization membership gains a real invitation flow (invite record with
  token + expiry, accept endpoint) instead of freeform member-id strings.
- **Workspace state becomes transactional**: the single unlocked
  `workspace_os.json` (lost updates, silent `[-200:]`/`[-500:]` truncation)
  is replaced by SQLite-backed workspace state in the brain DB family, with
  a one-time importer for existing JSON state. Truncation caps are removed —
  "knowledge is durable" applies to memories and timelines too.

## 6. Act: real runtimes — executing, durable, cancellable

- **Agent runtime**: orchestrator roles get an LLM-backed runner built on the
  *real* single-agent runtime (`core/agent.py`) + `llm_router` prompts
  (PLANNER/CRITIC already exist in `agent_prompts.py`). When no model is
  loaded, runs still work deterministically **but the run record persists
  `mode: "simulation"`** and simulated runs never write Experience records
  into the brain. The fabricated-provenance pathway dies. `AgentRegistry`
  entries become executable: a registered agent carries model id, system
  prompt, and a tool allowlist that the runtime actually loads — custom
  agents stop being a UI illusion.
- **Workflow runtime**: tool/skill nodes execute for real through
  `dispatch_tool` under the existing governance registry. Non-auto-approve
  tools produce an explicit `awaiting_approval` pause — never a silent
  `{recorded: true}` success.
- **Durable async execution** (in scope, not a gap): runs are persisted
  records (`queued → running → awaiting_approval → succeeded | failed |
  cancelled | interrupted`) executed as asyncio tasks on the server loop
  (synchronous tool/orchestrator work bridged via `asyncio.to_thread`;
  cross-thread bus publishes via `loop.call_soon_threadsafe` — the
  `RealtimeBus` is made thread-safe as part of this work). `stop()` performs
  real cooperative cancellation, checked between steps/tool calls — it
  cannot interrupt an in-flight MLX `generate()`, and agent generation
  serializes with interactive chat on the single inference thread; both
  limits are documented, surfaced honestly, never papered over. Progress
  streams over the existing `/realtime/stream` SSE endpoint (the
  `/agents/.../events` JSON snapshot remains as-is). On startup, any run
  left non-terminal by a crash/restart is reconciled to `interrupted` with
  reason + timestamp — no phantom "running" state survives a restart.
- **Per-tool approval gate**: when a run hits a non-auto-approve tool it
  pauses into `awaiting_approval`, surfaces the pending decision through the
  API/UI, and resumes (or aborts) on the recorded human decision —
  generalizing the proven `human_in_loop` plan-approval mechanism in
  `api/chat.py`. `approve()` stops auto-approving unconditionally.
- **Trigger system**: beyond `manual` — (a) interval/cron scheduling via a
  supervised scheduler loop; (b) **brain-event triggers**: workflows subscribe
  to ingestion lifecycle events through the existing hooks bus ("when a new
  document enters the brain, run this workflow"). Trigger firings create
  normal durable runs with provenance pointing at the triggering event.

## 7. Sovereignty: portability + Brain Network

- **Device identity**: per-installation Ed25519 keypair (`cryptography`, file
  + keyring storage). Every export and every peer interaction is attributable
  to a device the user controls.
- **Signed brain bundles**: exports (already sha256-manifested) gain a
  detached signature + device public key; imports verify and record origin
  provenance. Per-workspace export ("take your brain with you") joins the
  existing admin-global export. A bundle is a file — sneakernet is a fully
  supported transport.
- **Brain Network v1 (Knowledge Exchange)** — implemented, not just
  documented: explicit peer registry (name, base URL, trusted Ed25519 public
  key) with a deliberate pairing step; push/pull of signed workspace bundles
  between Lattice instances over plain HTTP (designed for LAN/tailnet —
  local-first, no cloud rendezvous, no relay service); the receiving brain
  verifies the signature against the *paired* key, imports through the normal
  ingestion/import path, and records origin-device provenance on every
  imported node. Peer requests authenticate independently of user sessions:
  each request carries an Ed25519 signature over (body digest + timestamp),
  verified against the paired key, with a freshness window + seen-nonce check
  for replay protection. Nothing is shared implicitly: exchange is
  per-workspace, per-request, owner-initiated. Identity-aware sync/merge
  conflict resolution beyond idempotent content-hash dedup is v1's documented
  boundary.
- **Compatibility policy for unsigned artifacts**: pre-v4 export bundles and
  backups have no signatures — local file imports/restores of them are
  accepted and recorded with provenance `origin='unsigned-legacy'`.
  Signatures are mandatory only on the Brain Network peer path. A v3.6
  export must always import into a v4 brain.
- **Agent collaboration across brains** inherits this substrate: an agent's
  Experience records travel inside bundles like any other knowledge, with
  provenance intact.

## 8. Surfaces

- **One frontend.** The v3 `/app` SPA is the product; legacy pages
  (`/chat`, `/graph`, `/workspace`, `/admin`, …) 308-redirect into `/app`
  routes. Login flows into `/app`. Legacy HTML/JS/CSS is deleted from the
  shipping set (kept in git history).
- **Brain-first IA**: nav regrouped (Brain · Ask · Capture · Act · Library ·
  System); the Knowledge Graph view is the post-login landing surface.
- **A real graph**: the force-directed canvas (drag/zoom/physics — already
  written in legacy `graph.js`) is ported into the v3 explorer, replacing the
  static SVG spiral.
- **Privacy honesty**: fonts/icons vendored locally; no CDN calls from a
  product that promises "nothing leaves your machine". Service worker
  precaches the v3 bundle, not the deleted legacy one. The hardcoded
  fusion-score meters in hybrid-search view are removed (real scores or no
  meters).
- **Login joins the design system**: the auth surface is rebuilt token-native,
  dropping its ~8,300 lines of legacy reference CSS.
- **i18n (en/ko)**: SPA strings externalized into a dictionary module with
  browser-locale default — formalizing the bilingual reality the legacy chat
  already proved demand for, instead of the current ko/en patchwork.

## 9. Backend shape: the brain gets a package

The dependency inversion (clean `latticeai/` importing dirty root modules)
ends:

- **`latticeai/brain/`** — the 4,633-line `knowledge_graph.py` single class is
  decomposed along its existing seams: `store.py` (authoritative v2 SQLite
  store), `extraction.py` (concept/triple extraction, LLM + rules),
  `documents.py` (pdf/pptx/docx/xlsx structure), `discovery.py` (local roots,
  audit, watch), `conversations.py` (durable conversation store, new),
  `memory.py` (memory-type model, new), `context.py` (ContextAssembler, new),
  `identity.py` (device keypair, new), `network.py` (peer exchange, new).
  A root `knowledge_graph.py` shim re-exports the public surface during the
  deprecation window so nothing external breaks.
- **Root modules are absorbed or deleted**: `setup.py` → `setup_wizard.py`
  (fixes both the broken wheel and the setuptools collision); `llm_router.py`,
  `mcp_registry.py`, `kg_schema.py` move under `latticeai/`;
  `telegram_bot` decouples from `api/chat.py` by subscribing to the
  `RealtimeBus` instead of being imported unconditionally; dead modules
  (`codex_telegram_bot.py`, `perm_monitor.py`) are deleted (git history
  preserves them). `knowledge_graph_api.py` is **live, not dead** (it serves
  the `/knowledge-graph/*` data endpoints the v3 SPA uses): its data router
  migrates into `latticeai/api/` with endpoint-parity tests; its legacy page
  routes join the frontend redirect work.
- **`create_app(config)` factory**: `server_app.py`'s import-time singleton
  construction and GPU side effects move into an explicit factory that builds
  the dormant `AppContext` dataclass and hands it to router factories —
  replacing 25-30-kwarg closure wiring with one typed context.

## 10. Platform / release engineering

- **Fix the broken wheel** (`setup.py` rename above) + an installed-wheel
  smoke test in CI: build, install into a clean venv, import, hit `/health` —
  from a non-repo cwd, so the class of "works in `pip install -e .` only"
  failures dies.
- ruff (lint) baseline + CI gate; bounded dependency constraints + lockfile;
  single `scripts/bump_version.py` writing all version copies (kept honest by
  the existing consistency test).
- npm tarball slimmed (no docs images / bots).
- `.gitignore` covers tarballs/logs/venvs; the 15MB tracked pptx is deleted
  at HEAD (stops the bleeding for new objects).

## 11. The only two exclusions, and why they are real blockers

Everything previously marked "foundation only" or "documented gap" is now in
scope (§3.1 v2 flip, §6 async/triggers/approvals, §5 identity+invitations+
transactional state, §7 Brain Network transport, §9 decomposition). Two items
remain excluded because they have *true* blockers, not effort blockers:

1. **Git history rewrite** (purging the pptx from past commits): requires
   force-pushing rewritten history to the shared remote — an irreversible,
   collaboration-breaking action that is reserved for the repository owner's
   explicit decision at RC review. v4 deletes the file at HEAD.
2. **Downloading a production embedding model silently at install**: violates
   privacy-first consent. The capability ships, but provisioning happens
   through the setup wizard with explicit user opt-in; until then the
   fallback embedder remains and reports itself honestly as
   `grade='fallback'`.

Neither exclusion hides behind fake UI: both are visible, labeled states.
