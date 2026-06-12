# Lattice AI v4.0.0 — Digital Brain Platform (Release Candidate)

> Status: **release candidate on `feat/v4-digital-brain`** — not merged, not
> tagged, not published. Validation: 571 unit tests pass, ruff clean,
> lint_v3 all checks pass, installed-wheel smoke verified.

v4.0.0 is a product transformation, not a feature release: the "Digital
Brain" identity v3.6.0 claimed becomes true in the implementation. The
release was driven by an 8-dimension evidence-based audit
(`docs/v4-audit/`), an adversarially-reviewed architecture
(`docs/V4_BRAIN_ARCHITECTURE.md`), and a normative implementation plan
(`docs/V4_IMPLEMENTATION_PLAN.md`).

## The headline: nothing fake survives

- **Workflows execute.** Tool nodes run through the shared governed tool
  lifecycle; tools requiring approval pause the run (`awaiting_approval`)
  with a durable cursor and a real resume/deny decision — the pre-v4
  `{recorded: true}` theater is gone. Skill nodes refuse honestly instead of
  reporting fake success.
- **The Multi-Agent Runtime is real when a model is loaded** (`mode: "llm"`):
  planner/executor/reviewer call the model; unparseable model output fails
  the run with the raw output preserved — never silently replaced by
  fabricated artifacts. Without a model, runs are honestly labeled
  `mode: "simulation"` and never enter the brain as experience.
- **Registered custom agents execute** with their persisted config; in
  simulation mode they skip with an explicit reason.
- Fabricated UI numbers (hybrid-search fusion meters), fabricated recall
  scores, and the dead memory-recall graph branch are fixed; README claims
  now match the implementation.

## One brain, no silos

- **Unified ingestion 4/5 → every chat message, MCP message, upload, browser
  capture, and garden note enters through one pipeline** with provenance and
  the hook lifecycle; `GET /knowledge-graph/provenance/coverage` reports
  coverage honestly (workspace events land with the T6 state rebuild).
- **Conversations are durable**: an unbounded SQLite store replaces the
  50-message chat_history.json cap; legacy history imports idempotently;
  backup/restore carries it automatically.
- **The garden vault stops being a second brain**: notes dual-write (vault
  markdown mirror for Obsidian + authoritative brain ingest); chat context
  queries the brain instead of rescanning the vault per message.
- **Typed memory**: Decision and Experience records as first-class graph
  nodes; agent learnings flow through the pipeline (no more markdown dumps
  with swallowed errors).
- **Context System**: chat context is assembled by a budgeted,
  provenance-carrying pipeline (workspace memories — injected at inference
  for the first time — + hybrid search + garden notes); the per-section
  trace persists with the answer ("why is this in my context?").

## Brain data model

- FTS5 trigram keyword index (Korean substring recall preserved; honest LIKE
  fallback; capability reported).
- Canonical edge taxonomy enforced at the write door (no new Korean
  free-string types; synonyms dedupe; originals preserved); `edges_v2`
  identity rebuilt so canonical types can't collide.
- Workspace scope columns (`workspace_id`, `visibility`, `owner_id`) with
  `legacy` semantics for pre-v4 rows; **search and graph reads filter by
  workspace membership** (Personal/Org Brain becomes real at read time).
- Temporal dimension: every edge observation recorded (`edge_occurrences`);
  node revision chains (`superseded_by`).
- graph_curator goes live: `POST /knowledge-graph/curate` runs gated topic
  promotion with real `importance_score` values.

## Sovereignty & Brain Network v1

- Per-installation Ed25519 device identity (file 0600 default; keyring
  opt-in); exports signed; tampered bundles refused; pre-v4 unsigned bundles
  import locally as `origin='unsigned-legacy'`.
- `export(workspace_id=…)` now really filters (the pre-v4 header lied).
- Peer exchange over LAN/tailnet HTTP: deliberate pairing by public key,
  signed + replay-protected requests, origin-device provenance on import.

## Triggers & automation

- Workflows fire beyond manual: interval scheduling (missed firings recorded
  as skips, no catch-up storms) and **brain-event triggers** — "when new
  knowledge enters the brain, run this workflow".

## Security

- By-id snapshot/memory endpoints authorize against the record's own
  workspace; the workspace registry no longer leaks member lists; chat
  context no longer absorbs other users' replies.
- Session tokens hashed at rest (transparent migration); real password
  policy; PKCE on SSO.

## Platform & UX

- The published wheel is fixed (the root `setup.py` application module is
  now `setup_wizard.py`, packaged, with an installed-wheel smoke test in CI).
- `create_app()` factory — importing the server performs no side effects.
- ruff lint baseline (repo clean, CI gate); bounded dependencies;
  requirements.txt retired; npm tarball 24.8MB → 2.0MB; 15MB pptx removed
  from HEAD.
- Zero CDN calls: fonts/icons/libs vendored; service worker precaches the
  v3 bundle.
- The Knowledge Graph explorer is a real force-directed canvas (drag, zoom,
  pan, physics) and **the landing surface**; navigation is brain-first
  (Brain · Ask · Capture · Act · Library · System).
- Agent and workflow runs use the durable async executor: queued/running/final
  lifecycle rows, realtime SSE progress, cooperative cancellation, and startup
  reconciliation for orphaned active runs.
- Identity and workspace governance are durable: users migrate to stable UUIDs,
  sessions keep email compatibility, policy enforcement is centralized in
  `core/policy.py`, invitations are real local tokens, and Workspace OS state
  imports into the shared SQLite database while retaining a JSON compatibility
  mirror.

## Remaining gaps (honest, labeled, contracted)

Tracked with full implementation contracts in
`docs/V4_IMPLEMENTATION_PLAN.md` + amendments and
`docs/V4_DIGITAL_BRAIN_RECOVERY.md`:

1. Legacy page deletion (requires parity views: org management, snapshots,
   activity, profile), token-native login, i18n, and the T9b surfaces
   (approval inbox, peer pairing UI, context-trace panel) — the new
   capabilities are API-complete and labeled API-only.
2. Git history rewrite for the removed pptx (owner decision; force-push).
3. Default production embedder (consent-gated wizard provisioning instead).
