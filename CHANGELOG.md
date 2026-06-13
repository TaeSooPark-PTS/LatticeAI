# Changelog

The detailed and current changelog lives in [docs/CHANGELOG.md](docs/CHANGELOG.md);
its top entry is the current release target (v4.4.0). Entries below
are an older root-level snapshot kept for reference and stop at 4.0.1.

## [4.0.1] - 2026-06-12

Maintenance release for changes on `main` after tag `v4.0.0`.

- Added the durable async agent/workflow run executor with realtime progress,
  cooperative cancellation, and startup reconciliation.
- Added stable user UUID migration, policy-backed admin authorization,
  invitation tokens, and SQLite-backed Workspace OS state mirroring.
- Closed the v4 SPA parity remainder: legacy static UI pages are removed,
  compatibility routes redirect into `/app`, token-native account/profile
  flows and en/ko i18n are live, and parity views cover workspaces,
  snapshots, activity, run approvals, workflow triggers, Brain Network, chat
  context trace, and KG provenance coverage.

## [4.0.0] - 2026-06-12 (release candidate)

Digital Brain Platform transformation. Highlights: workflow nodes execute
under governance with a real approval gate; LLM-backed multi-agent runtime
(fail-closed on unparseable output) with honest simulation labeling; unified
ingestion for chat/MCP/uploads with provenance coverage metric; unbounded
conversation store; garden vault absorbed (brain authoritative, markdown
mirror kept); typed Decision/Experience memory + budgeted provenance-traced
context assembly; FTS5 keyword search; workspace-scoped reads; by-id authz
fixes; hashed session tokens + PKCE; Ed25519 device identity, signed brain
bundles, Brain Network v1 peer exchange; interval + brain-event workflow
triggers; executable custom agents; packaging fixed (wheel smoke in CI),
ruff baseline, bounded deps; zero-CDN frontend with a force-directed graph
canvas as the brain-first landing surface. Post-tag implementation gaps are
closed in v4.0.1. See docs/v4-audit/ for the audit record and
docs/V4_BRAIN_ARCHITECTURE.md for the design.

## [3.6.0] - 2026-06-10

Knowledge Graph First release — the Knowledge Graph becomes the primary
architecture. Lattice AI is not a model-personalization system; it is a Digital
Brain Platform where the graph is the durable asset and models read it. Every
change is backed by automated tests. See
[RELEASE_NOTES_v3.6.0.md](RELEASE_NOTES_v3.6.0.md),
[docs/kg-schema.md](docs/kg-schema.md),
[docs/RUNTIME_HOOK_COVERAGE_v3.6.0.md](docs/RUNTIME_HOOK_COVERAGE_v3.6.0.md), and
[docs/CARRYOVER_AUDIT_v3.6.0.md](docs/CARRYOVER_AUDIT_v3.6.0.md).

### Added

- **Unified ingestion pipeline** (`latticeai/services/ingestion.py`) — one
  `IngestionPipeline.ingest()` entrypoint normalizes every source (file, folder,
  web URL, browser tab, text/markdown/note/code) into the graph, idempotent by
  content hash, bracketed by the `pre_tool`/`post_tool` lifecycle.
  (`test_ingestion_pipeline.py`.)
- **Entity/relationship model** — `Source`, `Repository`, `Meeting`,
  `Organization`, `Workflow`, `Agent` node types and `indexed_from`,
  `modified_by`, `belongs_to_project`, `part_of`, `discussed_in`, `decided_by`,
  `generated_by`, `used_by_agent` edges; additive + lossless `from_legacy`.
  (`test_kg_schema_v36.py`.)
- **Browser & web ingestion** — `POST /api/browser/read-url` and
  `/ingest-current-tab`, plus a Manifest V3 extension (`browser-extension/`) that
  posts only to `127.0.0.1`. (`test_browser_ingestion.py`,
  `test_runtime_coverage_v36.py`.)
- **Portability** — logical JSON export/import (versioned, merge/replace/dry-run)
  and binary backup/restore (DB + blobs, sha256-verified) via
  `latticeai/services/kg_portability.py` and
  `/api/knowledge-graph/{export,import,backup,restore,portability,provenance}`.
  (`test_kg_portability.py`.)
- **Provenance** — `ingestion_provenance` table + `record/get/list/
  provenance_stats`, an append-only trail making every node explainable.
- **UI** — Knowledge Graph view recast as the digital brain with Status, Sources,
  Capture, and Backup tabs.

### Changed

- KG ingestion now fires `pre_tool`/`post_tool` hooks (closes the one honest
  v3.5.0 carry-over gap). `docs/RUNTIME_HOOK_COVERAGE_v3.6.0.md` extends the v3.5.0
  coverage with no regression.
- README repositioned as a Digital Brain Platform (Knowledge Graph = durable
  asset; models replaceable; Vercel landing-only).

## [3.5.0] - 2026-06-09

Foundation stabilization & verification release — the last major hardening pass
before Knowledge-Graph-First (v3.6.0) and the Digital Brain Platform (v4.0). No
new product surface; every change is backed by automated tests + a live server
boot (`/health` → `3.5.0`). See [RELEASE_NOTES_v3.5.0.md](RELEASE_NOTES_v3.5.0.md),
[docs/RUNTIME_HOOK_COVERAGE_v3.5.0.md](docs/RUNTIME_HOOK_COVERAGE_v3.5.0.md), and
[FEATURE_STATUS.md](FEATURE_STATUS.md).

- **Security — OIDC verified, not decoded.** New fail-closed verifier
  (`core/oidc.py`, RSA/JWKS) checks signature, `iss`, `aud`, `exp`, `nonce`; the
  SSO callback no longer trusts a decoded `id_token` payload. `alg:none` / `HS*`
  rejected. Per-login `nonce` issued + enforced; `state` still enforced.
  (`test_oidc.py`, 15 cases.)
- **Security — proxy trust.** `client_ip` honours `X-Forwarded-For` /
  `CF-Connecting-IP` only from configured trusted proxies
  (`LATTICEAI_TRUSTED_PROXIES`); otherwise the peer is used, so per-IP rate limits
  can't be spoofed. (`test_proxy_trust.py`, incl. a bypass proof.)
- **Runtime — hook bypasses closed.** `read_file`/`edit_file`/`grep`/
  `clear_history`, the computer-use agent loop (`/cu/*`), and skill-eval now run
  through `dispatch_tool` (`pre_tool`/`post_tool`). Full table in
  `docs/RUNTIME_HOOK_COVERAGE_v3.5.0.md`. (`test_runtime_coverage.py`.)
- **Refactor — `tools.py` → `tools/` package** (computer / filesystem / documents
  / local_files / knowledge / network / commands + base + registry). Flat import
  surface 100% preserved; 46/46 tools registered; no circular imports.
- **CI — discover-based syntax gate.** `scripts/check_python.py` compiles all 144
  first-party modules (excludes venv/build/cache/vendored), auto-including future
  files. Wired into CI + `npm run check:python`.
- **UI — glassmorphism removed.** The v3 SPA scrim blur and 19 legacy
  `backdrop-filter: blur` surfaces removed; active v3 CSS has **zero** blur
  surfaces (assets rebuilt). 13/13 Playwright visual tests pass.

## [3.4.1] - 2026-06-08

Runtime completion release — makes the v3.4.0 runtime systems verifiably complete
and corrects the v3.4.0 overclaims an implementation audit found. Every change is
verified by a live end-to-end run (`docs/assets/v3.4.1/e2e_runtime_log.txt`,
7/7 PASS + restore-on-restart). See
[RELEASE_NOTES_v3.4.1.md](RELEASE_NOTES_v3.4.1.md) and
[FEATURE_STATUS.md](FEATURE_STATUS.md).

- **Hooks — full lifecycle.** Shared `dispatch_tool` (`core/hooks.py`) fires
  `pre_tool`/`post_tool` across the HTTP, agent (`core/agent.py`), and workflow
  (`platform_runtime`) tool paths (v3.4.0 only fired on the HTTP path). Workflow
  hooks fire from both the designer and platform paths. Explicit `pre_/post_` ×
  `run/tool/workflow/upload/index` lifecycle. **All 7 built-in hooks have real
  runners** (`core/builtin_hooks.py`); non-executable hooks are flagged
  `advisory`. Legacy `workflow`/`pipeline` kinds mapped forward.
- **Local Agent — real probes.** `/api/local-agent/status` no longer hardcodes
  readiness; it probes the filesystem (write/read/delete), graph reachability,
  and derives `mode` (online/degraded/error) + `pid`, `version`, handshake
  `latency_ms`, `last_seen`, `error`.
- **Connect Folder — proven end-to-end.** Real folder → approval → index → Files
  table → retrieval → hybrid search.
- **Folder Watch — proven end-to-end.** Create file → debounced reindex →
  `post_index` hook; `watchdog` installed; restore-on-restart verified.

## [3.4.0] - 2026-06-08

Platform completion release — closes the remaining non-enterprise functionality
gaps the v3.3.0 audit flagged. Every change is runtime-verified on a live server.
Enterprise features remain intentionally disabled. See
[RELEASE_NOTES_v3.4.0.md](RELEASE_NOTES_v3.4.0.md),
[PLATFORM_COMPLETION_REPORT_v3.4.0.md](PLATFORM_COMPLETION_REPORT_v3.4.0.md), and
[FEATURE_STATUS.md](FEATURE_STATUS.md).

- **Hooks execute** — real dispatch engine (`run_hook`/`run_hooks`/`fire_hook` +
  `HookContext`/`HookResult`), in-process runners for built-ins and subprocess
  execution for user hooks, `pre_*` gate semantics, a persisted run log
  (`/api/hooks/runs`), and on-demand `/api/hooks/run`. Fires from agents
  (pre/post-run), workflows (start/end), tools (pre/post-tool), and the upload
  pipeline. 17 new unit tests.
- **Uploads appear in Files** — `KnowledgeGraphStore.list_documents()` +
  `GET /knowledge-graph/documents`; the Files documents table re-hydrates after
  upload (upload → Files → Knowledge Graph → Hybrid Search → Chat).
- **VLM image input** — Chat composer attach/drag/paste/preview + a Vision
  Enabled/Disabled badge from a new `vision` block on `/models`.
- **Agent run trigger** — Run/Stop/Status/Queue/Logs console in the Agents view;
  runs without a model and fires pre/post-run hooks.
- **On-device Local Agent + Connect Folder + Folder Watch** —
  `GET /api/local-agent/status` (real runtime/handshake/health), `connectFolder()`
  self-approval flow, and `watchdog`-backed debounced reindex on change.
- **Public assets** — refreshed v3.4.0 screenshots under `docs/assets/v3.4.0/`
  (+ before/after), README + release history updated; stale screenshots removed.

## [3.3.1] - 2026-06-08

Visual product rebuild for the `/app` frontend. No package publication,
deployment, tag, or GitHub Release was performed.

- **Rebuilt `/app` shell** — denser command rail, grouped Basic/Advanced/Admin
  navigation, local index readiness footer, quieter topbar, and mode-aware
  command palette.
- **Rebuilt design system** — cooler neutral light/dark tokens, 8px card/panel
  radii, compact controls, redesigned stat cards, denser tables, improved empty
  states, and regenerated hashed v3 assets.
- **Rebuilt primary views** — Home is now a truthful readiness dashboard; Files
  separates manual upload from local-agent folder connection; Settings reports
  backend/local-agent/model/telemetry readiness; Chat send/stop streaming uses a
  stable handler.
- **Documentation** — added `VISUAL_REBUILD_NOTES_v3.3.1.md` and
  `FIGMA_SPEC.md`, and updated `STYLE_SYSTEM.md`.

## [3.3.0] - 2026-06-08

Product-quality and honesty release. No new product areas — the focus is
verifying what works, removing misleading states, and making the system
truthful and maintainable. See [FEATURE_STATUS.md](FEATURE_STATUS.md) for the
full evidence-based audit and [STYLE_SYSTEM.md](STYLE_SYSTEM.md) for the design
system.

- **Single source of version truth** — all runtime constants, `package.json`,
  `pyproject.toml`, the VS Code extension, lockfiles, and the v3 asset manifest
  report **3.3.0**. The build manifest now derives its version from
  `package.json`, and the Settings → About panel reads the live version from
  `/health` instead of a hard-coded string. New
  `tests/unit/test_version_consistency.py` guards against drift.
- **Working manual document upload (Files)** — the Files view drop zone is now a
  real uploader (drag-and-drop or picker) wired to the existing
  `/upload/document` parse → chunk → embed → knowledge-graph pipeline. PDF · DOCX
  · XLSX · PPTX · TXT · MD · CSV, ≤10 MB. Connecting a *folder* remains honestly
  disabled (it needs the desktop local agent, not in this build).
- **Fixed document-generation streaming** — the v3 chat SSE parser now accepts
  the document-generation event shape (`text`), so report/document requests
  render instead of falsely reporting the backend as unreachable.
- **Truthful Home retrieval status** — `/api/index/status` is normalized into the
  pipelines shape the Home pillars and topbar chip expect, so a live, indexed
  backend no longer shows a false "Retrieval status unavailable".
- **Honest copy** — chat grounding chips relabeled to describe the
  retrieval-context preview they actually drive; the Memory view no longer claims
  prune/clear controls it doesn't surface, and its recall copy reflects the
  workspace + graph scope it actually searches.

## [3.2.0] - 2026-06-08

Feature-complete release for all non-enterprise use cases. Every platform
capability is operable from `/app` with no Classic dependency.

- **Multi-agent collaboration** — Planner/Researcher/Executor/Reviewer with
  handoffs, shared context packets, review/retry, and replayable timelines.
- **Agent Registry** (`/agents/api/registry*`) — registration, discovery,
  metadata, versioning, capabilities, configuration; no hardcoded agent lists.
- **Marketplace & Agent Templates** — five named agent templates plus
  clone/export/import/install over the local catalog.
- **Workflow Agents & Autonomous Planning** — trigger → chain → tools → memory
  → result; goal → plan → execute → review → replan with inspect/replay.
- **Long-Term Memory + Memory Manager** (`/api/memory/*`) — workspace, project,
  agent, conversation, graph, and vector tiers unified, with recall, inspect,
  prune, compact, rebuild, and clear.
- **Skills, Hooks, Tool Registry, MCP Manager** — skills enable/disable/install;
  lifecycle Hooks registry (`/api/hooks/*`); governed tool registry; revived
  `/mcp/*`, `/skills/marketplace`, and `/plugins/directory`.
- **Eight new `/app` views** (memory, planning, workflows, marketplace, skills,
  hooks, tools, mcp) with honest live/unavailable states.
- **Release claim audit** — added `docs/V3_2_AUDIT.md`; wired Agent Registry
  controls into `/app#/agents`; fixed `/app#/skills` live registry payload
  handling; removed duplicate MCP route registration; expanded visual route
  coverage.
- Package publication and deployment were not performed.

## [3.1.0] - 2026-06-07

Mainline v3.1 platform completion for the non-enterprise local-first workspace.

- **Classic retired from normal workflows** — `/app` is the complete native
  surface for Chat, Models, Agents, Files, Pipeline, My Computer, Settings,
  Knowledge Graph, Hybrid Search, and Admin views. Classic routes remain as
  compatibility/debug surfaces only.
- **Native Models actions** — the Models view now calls `/models/load` and
  `/models/unload/{model_id}` directly instead of linking users to Classic.
- **Production embedding profiles** — `/api/embeddings/providers` exposes
  local, Ollama, MLX, and OpenAI-compatible production profiles for `bge-m3`,
  `nomic-embed-text`, `e5-large`, `gte-large`, `mxbai-embed-large`,
  BGE-M3-compatible providers, `text-embedding-3-small`, and
  `text-embedding-3-large`. Hash embeddings remain fallback-only.
- **No fake primary data** — v3 adapter fallbacks now render unavailable states
  with empty data instead of sample counters, sample graphs, or sample runs.
- **Hashed v3 assets** — added `npm run build:assets`, generated
  `static/v3/asset-manifest.json`, and switched `/app` to manifest-loaded
  hashed CSS/JS assets with no runtime `?v=` cache-busting.
- **Release sync** — Python package, npm package, VS Code extension, Workspace
  OS version, docs, and release artifact names are aligned at `3.1.0`.

## [3.0.1] - 2026-06-07

Release-blocker remediation: every v3 surface now works, is connected to its real
backend, or is clearly marked unavailable — nothing appears complete while
disconnected.

- **Provider-backed embeddings** — new `EmbeddingProvider` interface with
  `Hash` (offline fallback), `MLX`, `Ollama`, `OpenAI-compatible`, and `Custom`
  providers, selectable via `LATTICEAI_EMBEDDING_PROVIDER`. The knowledge graph
  takes an injected embedder; a configured provider that is unreachable degrades
  to the hash fallback and is reported as such (never silently faked). New
  `GET /api/embeddings/status` and `/api/embeddings/providers` surface provider,
  model, status, dimensions, and last index time in Settings → Models →
  Embeddings and the Models view.
- **AgentRuntime boundary** — agent execution, status, health, events, and
  configuration are unified behind `latticeai/services/agent_runtime.py` with
  `GET /agents/api/runtime/status|health|config`, `…/runs/{id}/events`, and
  `…/runs/{id}/stop`. The Agents view now reads real runs from the runtime
  instead of a fabricated ledger.
- **Frontend ↔ backend connections** — Agents (real runs + health), My Computer
  (local memory wired to `/workspace/computer-memory`, real activity), Admin
  Security (`/admin/security/overview`), Admin Permissions (`/admin/roles`),
  Admin Policies (`/admin/policies`, read-only enforced state), Settings
  (create-organization via `/workspace/orgs`), and Pipeline (working **Rebuild
  index** via `/api/index/rebuild`). Folder connect / per-file actions and other
  features without a wired backend are now labeled clearly unavailable.
- **Tests** — added `test_embedding_providers.py` and
  `test_agent_runtime_service.py`; the visual mock server exercises the new
  endpoints.

## [3.0.0] - 2026-06-07

Lattice AI v3 becomes the mainline local-first AI workspace platform. `/app` is
the primary product shell after login, with Native Chat, Knowledge Graph, Vector
Index, Hybrid Search, Files, Pipeline, Agents, Models, My Computer, Settings,
and Admin modes. Legacy `/chat` remains available as a rollback/debug path.

- **Integrated v3 retrieval** — Knowledge Graph, SQLite Vector Index, and Hybrid
  Search ship together with API contracts under `/api/search/*`, `/api/graph*`,
  and `/api/index/*`.
- **Native v3 Chat** — `/app#/chat` streams through `POST /chat`, keeps retrieval
  context in the shell, and shows a friendly no-model-loaded setup message.
- **Honest embedding status** — default vectors use `lattice-local-hash-v1`
  deterministic local fallback embeddings, not a production semantic embedding
  model.
- **Release safety** — tag builds validate exact `3.0.0` artifacts without
  publishing packages; package publication remains manual.

## [2.2.7] - 2026-06-05

Visual system stabilization. The rendered browser UI was reviewed screen by
screen and tightened so dark mode, light mode, modals, drawers, graph canvas,
Workspace OS, and Chat feel like one finished product.

- **Chat composer stabilized** — the bottom composer now renders as a crisp dark
  surface with a dark input shell, clear attachment/send controls, and no white
  haze or legacy inner textarea border.
- **Graph and Workspace surfaces corrected** — Knowledge Graph canvas uses the
  dark graph work-surface token, and Workspace OS list/input/card surfaces no
  longer revert to hardcoded white in dark mode.
- **Onboarding and modal language unified** — workspace select, onboarding,
  recommendation, auto setup, mode select, pipeline, My Computer, profile,
  settings, Private VPC, and model-state panels use the same dark modal/panel
  treatment.
- **Auth contrast sharpened** — account/register titles and inputs retain
  readable contrast in dark mode without glass haze.
- **Visual regression coverage** — added `tests/visual/v227.spec.js` to lock the
  composer, mobile composer, graph canvas, and Workspace OS dark surfaces.
- **Release sync** — package/runtime metadata and frontend cache-busting are
  aligned at `2.2.7`.

## [2.2.6] - 2026-06-05

Token-native CSS foundation. Eliminates the root cause of foggy/washed-out dark
mode instead of patching it with loaded-last overrides.

- **Legacy monolith removed** — the 7,985-line `static/lattice-reference.css` is
  split into token-native modules under `static/css/reference/`
  (`base`/`account`/`admin`/`graph`/`chat`) and deleted. The split is byte-for-byte
  concatenation-equivalent to the original cascade, so no layout regressed.
- **Dark mode fixed at source** — the chat skin no longer redefines color tokens
  with light literals; it is gated to the light theme, so dark mode inherits the
  `tokens.css` dark palette. Every active surface (canvas, sidebar, header, chat
  input, cards, modals, drawers, admin/VPC panels, onboarding) now uses
  `var(--token)`. ~180 hardcoded `#fff`/`white`/`rgba(255,255,255,…)`/lavender
  literals were converted to tokens; status/brand colors flip via semantic tokens.
- **Loading-order dependency gone** — the `:root[data-lt-theme="dark"]` override
  stack in `responsive.css` (~360 lines) is removed; theme correctness no longer
  depends on which stylesheet loads last. Active `!important` declarations: 0.
- **No full-screen blur / white scrims** — `backdrop-filter: blur` removed from
  full-screen overlays; page backgrounds route through the theme-aware `--app-bg`.
- **Stronger visual tests** — new `tests/visual/v226.spec.js` data-drives dark +
  light scans across account/admin/graph/chat (+ mobile, overlays open): no
  opaque-light surface in dark, WCAG text-contrast ≥ 2.2 in both themes, and no
  full-page backdrop blur.

## [2.2.5] - 2026-06-04

Release hygiene hotfix for dark-mode overlays, modal state, cache-busting,
favicon serving, and Telegram token logging.

- **Overlay root cause** — multiple full-screen overlays mixed translucent light
  violet/white backdrops with `backdrop-filter`, which made dark mode look
  foggy and washed out. Overlay backdrops now resolve through `--overlay-scrim`
  and disable blur for crisp dark contrast.
- **Modal state** — Chat overlays now go through a shared modal manager that
  enforces one blocking modal at a time, handles Escape and backdrop dismissal,
  clears stale overlays on navigation/pagehide, and restores body scroll lock.
- **Dark token cleanup** — added `--surface-muted`, `--surface-elevated`,
  `--text-muted`, `--overlay-scrim`, and input aliases, then mapped modal,
  drawer, local file, onboarding, model, pipeline, admin, and My Computer
  surfaces back to semantic tokens.
- **Release hygiene** — all versioned static assets now use `?v=2.2.5`;
  `/static/scripts/chat.js` is loaded as `/static/scripts/chat.js?v=2.2.5`.
- **Favicon** — added `static/favicon.ico` and a `/favicon.ico` route.
- **Telegram logging** — added token redaction so `bot123:secret` is logged as
  `bot123:REDACTED` across Telegram HTTP/exception logging.
- **Tests** — added unit tests for token masking, favicon/static asset hygiene,
  and Playwright coverage for modal stack/scroll-lock/permission restore.

## [2.2.4] - 2026-06-04

Chat dark-mode completion. Resolves the v2.2.3 known issue where the entire Chat
page rendered light in dark mode. All fixes keep the design-token system, add no
`!important`, use no inline-style band-aids, and do not regress light mode.

- **Root cause** — `body.lattice-ref-chat` (in `lattice-reference.css`) redefines
  16 color tokens (`--bg`, `--surface`, `--text`, `--border`, …) as *light
  literals*. Because `<body>` is a descendant of `:root`, those body-level values
  shadowed the `:root[data-lt-theme="dark"]` tokens, so every `var(--token)` on
  the chat page resolved light even in dark mode.
- **Fix** — added a `:root[data-lt-theme="dark"] body.lattice-ref-chat` block that
  re-points the same 16 tokens to the `tokens.css` dark palette (a proper theme
  branch, not a specificity trick), so all token-driven chat surfaces (body,
  bubbles, sidebar, header, composer, …) switch to dark at once.
- **Hardcoded-light surfaces** — surfaces that baked in light colors instead of
  tokens (sidebar, user strip, chat header, mode segmented control, logout/lang
  button, account/MCP/mode/workspace modals, modal inputs, composer box,
  workspace cards) were corrected with `[data-lt-theme="dark"]`-scoped overrides
  mapped to dark tokens (`--sidebar`, `--modal`, `--input`, …). Identified via a
  data-driven scan that walks every visible element for opaque-light backgrounds.
- **Toast** — moved off an inline `cssText` with hardcoded light colors to a
  token-driven `#ltcai-toast` CSS rule, so it adapts to light/dark.
- **Tests** — new `tests/visual/v224.spec.js` (14 tests): a zero-light-surface
  dark scan across shell + overlays + bubbles, dark surface/text checks, a
  light-mode non-regression guard, theme-aware toast, and 10-width responsive
  (375→3440) horizontal-scroll / composer-clip checks. Visual suite now 52 tests.
- Asset cache-busting bumped to `?v=2.2.4`.

## [2.2.3] - 2026-06-04

Frontend Stability & UX Fixes. A no-features stabilization release that fixes
real usability regressions found after v2.2.1 and hardens the UI. All fixes keep
the existing design-token system, add no `!important`, and avoid
specificity-only overrides.

- **Login input readability (dark theme)** — on the login screen the input field
  backgrounds were hardcoded light while the input text used a theme token that
  flips to near-white in dark mode, so email/password text was invisible
  ("white on white"). Added a proper `[data-lt-theme="dark"]`-scoped auth theme
  (dark glass card/titlebar/fields, light title/subtitle, dark SSO buttons) plus
  a Chrome/Safari autofill correction. Light theme is unchanged.
- **Recommendation result not clickable / not scrollable** — the onboarding
  recommendation step put its content (the Gemma 4 / Qwen3-VL / Llama 4 model
  accordions and the action buttons) in `#onboarding-body`, but the rule meant
  to make it scroll used a compound selector (`.onboarding-body.lattice-ref-chat`)
  that never matched, so the body had no scroll region and the card clipped it.
  Long content and the bottom buttons were unreachable. Fixed the scroll region;
  the accordions and actions are now reachable and clickable.
- **Recommendation dark readability** — the onboarding card and its inner cards
  were hardcoded light; re-pointed the overlay's tokens to the dark palette and
  tokenized the "Best for this PC" callout so the screen is readable in dark.
- **Button interaction stability** — verified clickability/hit-testing for login,
  onboarding, graph, and admin controls (no overlay/`pointer-events`/`z-index`
  blockers).
- **Playwright QA strengthened** — added login readability (light/dark),
  recommendation scroll + accordion + reachable-actions, dark-mode readability,
  and uncaught-page-error tests; total visual suite now 38 tests.
- Asset cache-busting query strings bumped to `?v=2.2.3`.

## [2.2.2] - 2026-06-04

Frontend QA stabilization release. No new features — this hardens the v2.2.x
UI and finalizes release documentation. Fixes were made at the source/override
layer with no `!important` and no change to the design-token structure.

- Fixed the mobile hamburger menus on the Knowledge Graph and Admin pages: the
  default-hidden rule for `.graph-nav-toggle` / `.admin-rail-toggle` was declared
  *after* its reveal, so source order kept the toggles hidden on phones and
  tablets and the navigation drawers were unreachable.
- Fixed Admin top-bar actions (Refresh, Logout) being unclickable: a
  graph-only `.toolbar { position: absolute; z-index: 20 }` rule had an
  over-broad selector that leaked onto the Admin/Chat form toolbars, floating
  them over the header and intercepting clicks. The toolbars are now scoped back
  to normal flow off the graph page.
- Fixed latent horizontal overflow on the Workspace page caused by a
  visually-hidden checkbox (`#computer-memory-toggle`) that stretched to the
  viewport width; it is now constrained to a 1px hit-box.
- Verified light/dark theme parity, no-horizontal-scroll, button hit-testing,
  mobile drawer open/close, and Escape-to-close across the full viewport matrix
  (375px phone → 3440px ultrawide) with an expanded Playwright suite.
- README finalized as a product landing page; asset cache-busting query strings
  bumped so the QA fixes ship to existing installs.

## [2.2.1] - 2026-06-04

- Shipped a mobile-first responsive UI across phone, tablet, laptop, desktop,
  ultrawide, and 4K. No content is hidden on smaller screens; it is only
  re-laid out.
- Added light/dark mode with OS detection, a manual toggle, and persistence.
- Rebuilt the design-token system as a single source of truth
  (`static/css/tokens.css`); theming no longer relies on `!important`.
- Improved accessibility: 44px touch targets, `:focus-visible` rings, a
  keyboard-safe chat composer (visualViewport inset), iOS no-zoom inputs, and
  reduced-motion support.
- Reworked the Knowledge Graph UX with a responsive canvas that re-fits on
  resize, zoom buttons, fullscreen, a minimap, a relationship filter, a mobile
  graph/card view, and a theme-aware palette.
- Reflowed wide admin tables into cards on mobile, with responsive layout,
  dark/light support, and larger touch targets.
- Added file attach by drag & drop and screenshot paste.
- Model cards now describe country, company, run mode, and internet usage in
  plain language.
- README, marketplace metadata, and release copy now present Lattice AI as a
  local-first AI workspace for knowledge graphs, AI pipelines, model workflows,
  and multi-agent coding workflows.

## [2.2.0] - 2026-06-04

- Reframed Lattice AI as a multimodal-first AI Knowledge Graph workspace.
- Removed current text-only local model recommendations and MLX-LM execution
  recommendation paths.
- Removed current Gemma 2, Gemma 3, Qwen2.5-VL, GPT-OSS, Phi, Mistral,
  DeepSeek, SmolLM, and Llama 3.x local recommendation entries.
- Added source disclosure metadata to recommended model catalog entries.
- Updated README, architecture, release notes, and model/graph policy docs.
- Bumped Python, npm, VS Code extension, and runtime version metadata to 2.2.0.
