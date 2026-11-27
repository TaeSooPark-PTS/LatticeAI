[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./FEATURE_STATUS.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](./docs/ko/FEATURE_STATUS.md)

# Lattice AI Feature Status (v14.3.0)

> **Status: canonical** — current-truth feature state, kept in sync with the
> current release.

Current release: **14.3.0 — Temporal Synthesis**.

This document records the live product capabilities and honest boundaries of
Lattice AI as of `v14.3.0`. Public release history is maintained from `v13.0.0`
onward in [`RELEASE.md`](RELEASE.md), [`RELEASE_NOTES.md`](RELEASE_NOTES.md),
and [`docs/CHANGELOG.md`](docs/CHANGELOG.md).

---

## Product Position

Lattice AI is a **local-first Digital Brain**. The durable asset is the user's
Brain — conversations, documents, decisions, memories, provenance, and
Knowledge Graph structure — while local or opt-in cloud models act as
replaceable runtimes over that Brain.

- **Quiet workspace, not an admin dashboard:** The first viewport centers on the
  Living Brain and the composer across desktop (`1280×800`) and mobile
  (`390×780`), with capture integrated into the composer and everyday
  destinations (`Chat · Library · Memory`) separated from management surfaces
  (`Work · AI Models · Settings`).
- **One Door Rust product server:** `lattice-host` natively serves **422
  operations across 41 route families** across 9 Rust workspace crates, owns
  all Knowledge Graph writes through a single `GraphWriter` / `Store::write_conn`
  pool, and supervises a 21-route loopback Python AI compute worker.
- **Honest grounding & proposal-first governance:** Hybrid retrieval reports
  context quality and claim-level verification transparently. Additive creations
  run with minimal friction; edits or deletions of existing user files,
  Self-Model updates, contradiction resolutions, folder reorganizations, and
  cloud-extracted knowledge stage as hash-checked Review Center proposals before
  applying.
- **Explicit autonomy & network boundaries:** `PermissionMode` (`strict` /
  `trusted` / `bypass`) and `NetworkBoundaryMode` (`local_only` default /
  `cloud_allowed` opt-in) are orthogonal user/workspace controls, while
  destructive-command circuit breakers and sensitive-node filters remain
  mode-invariant.

---

## Current Feature Status

| Area | Status | Notes |
| --- | --- | --- |
| **One Door Rust Product Server** | Current | Nine workspace crates (`lattice-host`, `lattice-core`, `lattice-auth`, `lattice-agent`, `lattice-retrieval`, `lattice-ingest`, `lattice-jobs`, `lattice-platform`, `lattice-chat`) serve 422 native operations across 41 route families. The Python AI worker serves strictly 21 allow-listed compute routes (`rust/fixtures/worker_allowlist.json`); all other non-native paths return `404`. |
| **Canonical Crate Domain Structure** | Current | `lattice-agent` is organized into 6 groups (`kernel`/`parse`/`content`/`tools`/`surface`/`prompts`) and `lattice-platform` into 7 domains (`workspaceos`/`toolsurface`/`governance`/`adminops`/`knowledge`/`modelops`/`shell`). All workspace callers use canonical domain paths (obsolete crate-root compatibility maps removed in `13.7.2`), enforced by `domain_boundaries.rs`. |
| **Unified UTC Clock & Single-Writer SQLite** | Current | All Knowledge Graph mutations serialize through `lattice-core::graph_write::GraphWriter` and `Store::write_conn`. Rust (`clock.rs`, zero `libc` `unsafe`) and Python (`lattice_brain/utils.py`, `_kg_fsutil.py`) emit unified offset-free UTC timestamps (`YYYY-MM-DDTHH:MM:SS`) with zero skew across timezones (`Asia/Seoul`, `America/New_York`, `UTC`). Restoring a `.latticebrain` archive bumps the store generation epoch for instant in-process visibility with no restart. |
| **Brain Home & Everyday Surfaces** | Current | Living Brain growth rings, composer-first workflow, floor continuity bar (`Past Chats · Overview · Memory Map · Features`), per-route `React.lazy` code splitting (`<= 150 KiB` gzip initial JS budget), per-route i18n namespaces (`ko` default + `en`), and route/panel `ErrorBoundary` isolation. Strict TypeScript 7.0.2 (`noUncheckedIndexedAccess`) at 100% Vitest coverage (`113` files, `1,852` tests). |
| **Grounded Chat & File Generation** | Current | Refuses to fabricate model output when no model is loaded; renders source proof, `ClaimVerificationStrip` telemetry, one-click **Save to Brain**, inline staged proposal diffs (`InlineStagedProposals`), live active-tool step indicators, and governed file creation (HTML, DOCX, PDF via worker render seams). Durable conversation document sessions persist across restarts without blocking the Tokio runtime. |
| **Knowledge Graph & 2-Hop Neighborhood Synthesis** | Current | Interactive explorer (`DigitalBrainExplorer`), `GraphNodeInspector`, single-query 2-Hop reasoning chains and bridge concepts (`neighborhood_synthesis.rs`, `MultiHopSynthesisView`), contradiction-only filtering, `ContradictionInspector`, `/` and `Escape` shortcuts, and `⌘K` node focus. |
| **Brain Governance UI Panels** | Current | Five dedicated lazy-loaded React panels in Brain wired to the unified `brain_api` OpenAPI contract (`IntegrityRepairPanel`, `EntityMergePanel`, `KnowledgeCorrectionsPanel`, `EmbeddingRegistryPanel`, `ClustersGapsPanel`), alongside `SelfModelPanel` and `BrainIntelligencePanel`. |
| **Bi-Temporal Knowledge & Chronicle** | Current | Nodes and edges track `valid_from` / `valid_to` / `superseded_by` and `recorded_at` / `superseded_at` (`temporal.rs`). Distinguishes `TemporalEvolution`, `ComplementaryRefinement`, and `ActiveContradiction`. **Brain Chronicle (`#/chronicle`)** renders SVG growth curves, activity heatmaps, daily stories, and `as_of` point-in-time rewinds. |
| **Entity Resolution & Selective Rollback** | Current | Type-scoped clustering (`(node_type, key)`) prevents cross-type homonym merges, paired with fuzzy multi-signal scoring, Korean particle stripping, persistent `entity_aliases` / `entity_merge_audit` / `entity_do_not_merge` tables, strict cross-workspace 403 ownership enforcement, non-destructive `SAME_AS` links, and selective `rollback_entity_merge`. |
| **KG Integrity Checker & Safe Repair** | Current | Workspace-scoped corruption, dangling-edge, orphan chunk/vector, cross-workspace leak, duplicate natural-key, index-drift, and DAG-cycle detection (`lattice-core::integrity`), dry-run blast-radius previews, automatic `VACUUM INTO` snapshot backups before repair, and admin-gated global mode. |
| **Claim-Level Grounding & Authentic RAG Eval** | Current | `grounding.rs` checks answer claims against cited chunks for support rate, citation precision, numeric/polarity conflicts, and superseded-source leakage, recommending abstention when unsupported. `rag_eval_grounded.rs` and `datasets/rag_eval_v1.json` (320 queries, 9 categories) separate deterministic fixture evals from live local/cloud model runs (`.github/workflows/nightly-rag-eval.yml`). |
| **3-Way Hybrid Search & GraphRAG** | Current | Combines pre-tokenized Okapi BM25F (`bm25_node_stats`, two-stage Korean particle stripping, CJK bigrams, hub fan-out caps), dense/subword vector search, and 1-hop/2-hop Knowledge Graph expansion via Reciprocal Rank Fusion (RRF), with containment deduplication, lexical phrase reranking, unified `score_explanation`, and explicit `fallbacks`. |
| **Section Tree & Structure-Preserving Ingestion** | Current | Writes `Document ←PART_OF— Section —HAS_CHUNK→ Chunk` outlines alongside structured table, page, slide, sheet, caption, and optional local OCR/vision overlays (`document_structure.rs`). |
| **Folder Ingestion, Debounced Watch & Incremental Sync** | Current | `.latticeignore` filtering, fingerprint skip (path + size + mtime + sha256; no-change reindex `33s → 0.26s`), `spawn_blocking` filesystem I/O, `DebouncedWatchEngine` (`moved_from_uri` relocation + per-watch `paused`), and two-phase async chunk-level incremental re-embedding (`incremental_sync.rs`, `watch_deliver.rs`). |
| **Deleted-File Cleanup** | Current (consent-gated) | Incremental ingest reports vanished files without auto-deleting. `POST /api/ingestion/folder/prune` previews dry-run counts and, upon explicit `confirm`, removes only the graph subtree (`delete_document_tree` / `delete_node_subgraph`). Disk files are never touched. |
| **Vector Index Backends & Embedding Registry** | Current | Unified Rust ↔ Python `EmbeddingIndexContract` (`provider`, `model_id`, `model_revision`, `dimension`, `normalization`, `index_version`, `grade`, `semantic`). Supports `brute` (default exact scan), `quantized`, and bounded LRU `NativeHnswIndex` / worker `hnsw+rescore` (auto-tried at 512+ vectors with a bound worker). `EmbeddingModelRegistry` supports resumable shadow re-indexing, quality-gated cutover, and instant rollback. |
| **Temporal Retrieval & Chunking** | Current | Token-sized parent-child chunks with contextual prefixes, tree-sitter code splitting, time-intent prefilter, recency decay, `SUPERSEDES` handling, `sort_by=relevance\|newest`, and `ltcai search`. |
| **Reranking & Chat Grounding Gates** | Current (reranker needs `rag` extra) | Cross-encoder rerank through `POST /worker/rerank` with RRF fallback, MMR with a per-document cap, and Self-RAG abstention. Real reranker and EmbeddingGemma 2 loading is unverified in the build environment. |
| **3D Living Brain & Desktop Shell** | Current | 3D graph view with retrieval firing and 2D fallback, fixed-viewport shell, Brain Vital Pill, docked evidence panel, and `Cmd/Ctrl+K` palette. |
| **Background Embedding & Drain Controls** | Current | Durable `vector_jobs` queue drained by `lattice-jobs` adaptive scheduler (preserving partial drain progress and guarding `/host/jobs` with loopback/Bearer checks). `VectorFreshnessNotice` surfaces embedded-vs-total progress and one-click **Index now** (`POST /api/index/drain`). |
| **Proactive Synthesis, Learning Loop & Self-Model** | Current | Event-driven synthesis stages `kg_change_digest` proposals for contradictions, unnamed topics, unlinked pairs, and stale fragments. Local-only `knowledge_corrections` learning loop records user corrections with workspace ownership checks. `SelfModelPanel` and `/api/memory/self-model*` manage owner preferences, habits, decisions, and relationships with proposal-first writes. |
| **Universal Adaptive Agent Harness & Durable Workflows** | Current | Model-agnostic `kernel/probe.rs` 3-turn micro-probing picks `standard` / `compact` / `guided` dials with zero per-model branches, downward-only self-demotion (`demote_to_guided`), `ui_resolve.rs`, `rag_grounding.rs`, and fail-closed `NEEDS_REVIEW` verification. `DurableWorkflowStore` provides atomic checkpoints, SHA-256 idempotency replay, `WaitingApproval` restart recovery, file rollback snapshots, and 5-role least-privilege `MultiAgentSession`. |
| **Change Governance & Reversible Reorganization** | Current | `MUTATING_TOOL_INVENTORY` ensures every mutating tool is governed or explicitly exempt. Existing-file edits/deletions stage as base-SHA-verified `change_proposal` items (`409` on conflict). Folder reorganization proposes moves into `topics/<topic>/` (zero delete path) and generates a one-click Undo proposal upon apply (`reversible_reorganization.rs`). |
| **Permission Modes & TOCTOU-Safe Sandbox** | Current | Per-user/per-workspace `strict` / `trusted` / `bypass` dial with mode-invariant circuit breakers. Per-user `AGENT_ROOT` isolation (`IsolatedRootSpec`) and OS-level TOCTOU-safe workspace I/O (`safe_io.rs`: Unix `openat`/`O_NOFOLLOW` + Windows handle-pinned `CreateFileW` component traversal). |
| **Network Boundary & Hybrid Cloud Chat** | Current (opt-in) | Defaults to `local_only`. Opt-in `cloud_allowed` enforces bounded LRU token budgets (`cloud/budget.rs`), secret-file blocking (`.env`, `.pem`, `.key`), PII/secret redaction (`lattice-core::redact`), shape-only `cloud_egress` audits, and `kg_cloud_expansion` proposals. Supports `api_key` (`GET /models` live probe) and `cli_oauth` (`agy` / `grok`). |
| **Cross-Platform Local Models & Cooperative Cancel** | Current | Hardware-aware catalog (`Origin Country → Vendor → Execution`) and one-click engine setup across macOS (`omlx` tiered RAM/SSD KV cache, platform-guarded `local_mlx`, `ollama`, `lmstudio`, `llamacpp`), Windows (`winget` + `pip` `llama-cpp-python[server]` fallback), and Linux (`ollama`, `llamacpp`, `vllm`), including `Qwen3.8-27B-4bit`, Gemma 4, and EmbeddingGemma 2 with live SSE progress and cooperative cancellation (`AbortController` + `499`). |
| **Tool Registry & Native MCP Server** | Current | `POST /mcp` is a native streamable-HTTP JSON-RPC server inside the OpenAPI product contract (`initialize` / `tools/list` / `tools/call`) sharing `check_governance` with the agent catalog (`mcp.<tool>` and `skill.<name>`). |
| **Feature Toggles** | Current | Server-rendered **Features** drawer (`GET/POST /api/features`, precedence `user → env → default`) toggles multimodal, video ingest, vault watch, Brain Network, synthesis, inline vector sync, RRF fusion, graph expansion, and vector backend without restart. |
| **Native Local Interop Bridges** | Current (opt-in) | Local-file-only native Rust `GraphWriter` ingestion for Obsidian vaults (`[[wikilink]]` `LINKS_TO` edges), Notion exports (directory/`.zip`), local Git commit logs, and `.eml`/`.ics` files behind approval tokens. |
| **Encrypted `.latticebrain` Archives & Brain Network** | Current | Rust-backed RFC 7914 `scrypt` passphrase or `X25519` + `HKDF-SHA256` recipient encryption, `SHA256-CTR` + `HMAC-SHA256` constant-time verification, and ZIP path-traversal blocking. Opt-in selective subgraph sharing (`LATTICEAI_BRAIN_NETWORK=1`) uses Ed25519 signatures and Review Center proposals. |
| **Desktop Shell (Tauri 2.11) & Extensions** | Current | Serialized async Tauri gateway lifecycle with `PortPolicy::PinnedProduction` fail-fast on port `4825`, strict CSP without `'unsafe-inline'`, direct `BackendStatus` infallible commands, and `spawn_blocking` folder picking. Hardened VS Code extension (Bearer auth, TTL approval cache, live `agent_step` timeline, cross-platform VSIX) and Browser extension (port-4825 permissions, live DOM visibility capture, UTF-8 byte truncation, server language sync). |
| **Multimodal (Image, Audio, Video)** | Opt-in (default off) | Behind `allow_multimodal`: first-class `Image`/`ImageText`, `Audio`, and `Video` (`Modality::Video` + `ffmpeg` keyframes + `.srt`/`.vtt`) nodes. OCR (`pytesseract`), VLM captions, vision embeddings, transcribers, and `ffmpeg` are not bundled and report `unavailable` honestly when absent. |
| **PostgreSQL Backend** | Planned | SQLite WAL is the official live local Brain store. PostgreSQL/pgvector remains `Planned` (dry-run planning and opt-in dispatch CI guard only). |
| **Telegram Bridge & SSO/OIDC Login Callbacks** | Removed (11.6.0) | Retired when the product server moved to Rust (`lattice-host`). Password authentication, sessions, `$scrypt$` hashing, rate limiting, CSRF, and SSO configuration surfaces remain native in `lattice-auth`. |
| **Release Artifacts & Cross-Platform CI/CD** | Current | Exact-version Python wheel/sdist, npm tarball, VS Code VSIX, macOS Apple Silicon DMG (`Lattice AI_14.3.0_aarch64.dmg`), and `SHA256SUMS`, verified by Ubuntu + Windows Rust CI, macOS Tauri CI, digest-pinned Docker smoke CI, and PR + `main` Playwright visual smoke. |

---

## Known Limitations (Honest Boundaries)

- **Retrieval models unverified here:** real EmbeddingGemma 2 and
  `bge-reranker-v2-m3` loading was not exercised for 14.2.0; failures degrade to
  hash embeddings and RRF order.
- **No per-user document privacy inside a workspace:** the multi-user viewer
  filter covers conversation-derived nodes only.
- **Small-model content quality on hard tasks:** The Universal Adaptive Agent
  Harness solves mechanical execution (`28.0% → 84.0%` on
  `Qwen2.5-0.5B-Instruct-Q4_K_M`), yet 0.5B–2B models can still produce fluent
  but incomplete summaries on open-ended tasks. Fail-closed verification routes
  those runs to `NEEDS_REVIEW` rather than reporting false success.
- **Cloud `api_key` live billing boundary:** `api_key` mode live-probes
  `GET /models` with zero billing and fail-closes when unreachable; billed chat
  completions are not executed in CI (live E2E is verified with zero-billing
  `cli_oauth`).
- **macOS DMG code signing:** `.github/workflows/release.yml` signs and
  notarizes with Developer ID when `APPLE_CERTIFICATE` secrets are configured
  and falls back to ad-hoc signing otherwise (first launch uses standard
  Gatekeeper approval). `npm run release:validate` verifies artifact presence
  and naming, not Developer ID signatures.
- **Default vector backend environment variable:** `LATTICEAI_VECTOR_INDEX`
  defaults to `brute` (while `hnsw+rescore` auto-activates at 512+ vectors with
  a bound worker). Flipping the default env remains gated on continuous-ingest
  live recall@10 measurement. The `quantized` int8 backend is ~2.2× slower than
  `brute` at 10k rows without meaningful peak-RSS savings and is not recommended.
- **Hugging Face runtime commit pinning:** `scripts/verify_hf_model_registry.py`
  statically validates Hub metadata and sizes without downloading weights, while
  runtime downloads in `model_runtime/download.py` and
  `embedding_providers/text.py` do not yet enforce default immutable `revision=`
  pinning across all unpinned loads (tracked as Bandit `B615` in
  [`docs/SECURITY_AUDIT.md`](docs/SECURITY_AUDIT.md)).
- **Unbundled vision/speech models & `ffmpeg`:** OCR, VLM captioning, shared-space
  vision embeddings, ASR transcribers, and `ffmpeg` are optional external
  dependencies; missing components report `unavailable` without fabricating
  content. Image/video observation helpers stay under Brain Core unit tests
  without a standalone HTTP route.
- **Image evidence thumbnails:** Evidence cards render 96px inline `data:image/`
  thumbnails capped at 24 KB rather than exposing raw disk paths without
  explicit `/local/serve` approval.
- **Local-export-only interop bridges:** Notion API, IMAP, and macOS
  Calendar/Mail live connectors are intentionally out of scope; interop bridges
  ingest user-exported local files only.
- **Pointer tools (`pyautogui`):** The six pointer tools execute in the Python
  worker (`pip install "ltcai[pointer]"`) rather than a native Rust OS actuator.
- **Self-Model extraction scope:** Phrase-table extraction (EN/KO) is
  proposal-first with no LLM `refiner` wired yet, and `open_keys` filters
  `pending` proposals only.
- **Coverage floor asymmetry:** Frontend Vitest enforces 100% across statements,
  branches, functions, and lines (`all: true`); Python CI enforces a `>= 90%`
  line-coverage floor.
- **Local OS security boundary:** On-disk privacy depends on the host OS user
  account, disk encryption, and external backup hygiene.

---

## Release History Kept In Git (`v13.0.0+`)

The active Git tree maintains public release history for the modern Rust-host
and domain-grouped architecture era (`v13.0.0` through `v14.2.0`):

- `14.2.0` · `14.1.2` · `14.1.1` · `14.1.0` · `14.0.0`
- `13.9.0` · `13.8.0` · `13.7.2` · `13.7.1` · `13.7.0` · `13.6.0` · `13.5.0` ·
  `13.4.2` · `13.4.1` · `13.4.0` · `13.3.1` · `13.3.0` · `13.2.1` · `13.2.0` ·
  `13.1.0` · `13.0.0`

See [`docs/CHANGELOG.md`](docs/CHANGELOG.md),
[`ARCHITECTURE.md`](ARCHITECTURE.md), and [`SECURITY.md`](SECURITY.md) for full
details.
