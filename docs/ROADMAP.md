[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./ROADMAP.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](./ko/ROADMAP.md)

# Lattice AI Gap Roadmap

> **Status: reference** — point-in-time gap inventory, refreshed at **v14.1.0**
> (2026-11-25). Not a canonical current-release document; version gates do not
> bind it.

As of **2026-11-25**, against the **14.1.0 — Living Synthesis** tree. This page
tracks genuine remaining engineering gaps sourced from
[`FEATURE_STATUS.md`](../FEATURE_STATUS.md),
[`docs/SECURITY_AUDIT.md`](SECURITY_AUDIT.md), and the active `v13.0.0+` release
notes in [`docs/releases/README.md`](releases/README.md). Crate ownership maps
live in [`rust/lattice-agent/ARCHITECTURE.md`](../rust/lattice-agent/ARCHITECTURE.md)
and [`rust/lattice-platform/ARCHITECTURE.md`](../rust/lattice-platform/ARCHITECTURE.md);
the contributor guide is [`docs/DEVELOPMENT.md`](DEVELOPMENT.md).

Difficulty: **S** one focused change · **M** a vertical slice · **L** a
multi-part program (credentials, model harness, index format, or platform port).

---

## Recently Closed (`v13.0.0` – `v14.1.0`) — Do Not Reopen

| Item | Shipped In | Resolution |
| --- | --- | --- |
| Self-Model & Brain Governance UI panels | `13.1.0` / `13.9.0` | Added `SelfModelPanel` plus five lazy-loaded Brain Governance panels (`IntegrityRepairPanel`, `EntityMergePanel`, `KnowledgeCorrectionsPanel`, `EmbeddingRegistryPanel`, `ClustersGapsPanel`) wired to unified `brain_api` OpenAPI types. |
| Cooperative model download cancellation | `13.1.0` | Added end-to-end `AbortController` + HTTP `499` cancellation and byte/filename SSE progress across `InstallScreen.tsx` and `ModelsPanel.tsx`. |
| Server-side i18n ratchet & Rust catalog | `13.0.0` | Locked all 6/6 worker routers and unified 146 Rust server messages in `lattice_core::messages` verified by `scripts/check_server_i18n.mjs`. |
| One-click reversible folder reorganization | `13.0.0` | `reversible_reorganization.rs` and `ReviewCard.tsx` emit a one-click Undo proposal whenever a reorganization applies. |
| Single-transaction review store mutations & durable doc sessions | `13.0.0` / `14.0.0` | Collapsed review mutations into single transactions and persisted `chat_document_sessions.json` via non-blocking `spawn_blocking`. |
| Browser extension server-language sync | `13.1.0` | Synchronized extension UI language with the server preference instead of relying solely on browser locale. |
| Bounded HNSW & cloud budget caches + `NativeHnswIndex` | `13.8.0` / `14.0.0` | Added pure-Rust `NativeHnswIndex` LRU cache (32 graphs) and bounded `cloud/budget.rs` (4,096 scopes) with poison recovery. |
| Cross-domain crate decoupling & legacy map removal | `13.0.0` / `13.7.2` | Removed flat crate-root `pub use` compatibility maps from `lattice-agent` and `lattice-platform`; enforced canonical domain imports via `domain_boundaries.rs`. |
| Universal Adaptive Agent Harness (0.5B–7B reliability) | `13.4.0` | Lifted 25-task empirical pass rate on `Qwen2.5-0.5B-Instruct-Q4_K_M` from `28.0%` to `84.0%` with zero per-model branches (`docs/ADAPTIVE_HARNESS_BENCHMARK.md`). |
| Real Windows Rust CI, macOS Tauri CI, Docker smoke & PR visual smoke | `14.0.0` / `14.1.0` | Added `rust-windows` (`windows-latest`), `tauri-check` (`macos-latest`), digest-pinned `docker-smoke`, and PR + `main` Playwright `visual` smoke in `.github/workflows/ci.yml`. |
| Rust ↔ Python UTC skew & Tauri production port drift | `14.1.0` | Unified timestamps onto offset-free UTC (`YYYY-MM-DDTHH:MM:SS`) across timezones and pinned production Tauri builds to port `4825` via `PortPolicy::PinnedProduction`. |
| Full-stack strictness (`BLE`, McCabe <= 20, TS `noUncheckedIndexedAccess`) | `14.1.0` | Narrowed all Python blind `except Exception` catches, lowered McCabe complexity cap to `20`, and resolved all 457 `noUncheckedIndexedAccess` TypeScript errors at 100% Vitest coverage. |

---

## P1 — High-Impact Quality & Supply-Chain Gaps

| Item | Why It Remains | Difficulty | Priority | Suggested Path |
| --- | --- | --- | --- | --- |
| **Small-model (0.5B–2B) open-ended content quality** | Mechanical tool execution and grounding are solved (`84.0%` on 0.5B), and fail-closed verification routes weak outputs to `NEEDS_REVIEW`, but 0.5B–2B models can still produce fluent yet incomplete prose summaries on complex multi-file synthesis. | L | **P1** | Keep measuring empirically with `probe.rs` and `rag_eval_v1.json`; improve structured evidence scaffolding without adding per-model branches. |
| **Hugging Face runtime commit `revision=` pinning (Bandit `B615`)** | `scripts/verify_hf_model_registry.py` statically checks Hub metadata and `EmbeddingIndexContract` records `model_revision`, but runtime downloads in `model_runtime/download.py` and `embedding_providers/text.py` still allow unpinned default revisions when not explicitly supplied. | M | **P1** | Store verified Hub commit SHAs in the catalog manifest and pass `revision=` + SHA-256 verification by default while preserving an explicit opt-in override for custom user repos. |

---

## P2 — Important Platform & Verification Gaps

| Item | Why It Remains | Difficulty | Priority | Suggested Path |
| --- | --- | --- | --- | --- |
| **Signed & notarized macOS DMG by default** | `.github/workflows/release.yml` supports Developer ID signing + notarization when `APPLE_CERTIFICATE` secrets are configured, but falls back to ad-hoc signing otherwise (requiring Gatekeeper approval on first launch). | M | **P2** | Configure repository Apple Developer ID + notarization secrets and extend `release:validate` with an optional `--require-signed` check. |
| **Live billed `api_key` completion contract test** | `api_key` mode live-probes `GET /models` with zero billing and fail-closes on errors, while live E2E chat completions run via zero-billing `cli_oauth` (`agy`/`grok`) and mock contracts. | S | **P2** | Add an opt-in scheduled/manual live completion contract smoke once a dedicated CI API budget is provisioned; keep `local_only` winning per request. |
| **Default `LATTICEAI_VECTOR_INDEX` flip (`brute` → `hnsw`)** | `hnsw+rescore` auto-activates at 512+ vectors with a bound worker and `NativeHnswIndex` is LRU-bounded, but the env default stays `brute` until recall@10 vs. exact scan is re-verified under long-running continuous ingest/delete churn. | M | **P2** | Run a continuous-ingest/delete recall@10 benchmark against `rag_eval_v1.json` before flipping the default. |
| **Nightly real-model RAG eval runner** | `.github/workflows/nightly-rag-eval.yml` is wired and reports `skipped`/`unavailable` honestly when no model endpoint is configured, because standard hosted GitHub runners lack Apple Silicon MLX / local GPUs. | L | **P2** | Bind a self-hosted GPU/Apple Silicon runner or managed eval endpoint so nightly real-model scores publish automatically. |

---

## P3 — Honest Polish & Optional Extensions

| Item | Why It Remains | Difficulty | Priority | Suggested Path |
| --- | --- | --- | --- | --- |
| **Approval-gated original image viewer** | `Image` citations render 96px inline `data:image/` thumbnails (capped at 24 KB) so disk paths are never exposed without consent. | M | P3 | Add an explicit approval-gated preview modal backed by `/local/serve` while retaining inline thumbnails as the zero-approval preview. |
| **Optional LLM `refiner` for Self-Model extraction** | Current EN/KO phrase-table extraction is deterministic and safe because every candidate lands in Review Center, but non-standard phrasings can be missed. | M | P3 | Wire an opt-in local model `refiner` behind a feature flag; keep proposal-first approval mandatory. |
| **Self-Model `open_keys` `snoozed` alignment** | Currently checks `pending` proposals only so snoozed items do not silently block re-proposals. | S | P3 | Decide explicitly with a regression test whether `snoozed` should suppress duplicate proposals. |
| **Dedicated Brain Network share UI** | Selective X25519/Ed25519 subgraph sharing works behind `LATTICEAI_BRAIN_NETWORK=1` and lands in Review Center, without a standalone peer-management screen yet. | M | P3 | Add a peer/share drawer under Brain only when multi-device sharing exits prototype status. |
| **PostgreSQL / pgvector live migration** | SQLite WAL is the canonical single-writer store; Postgres export/status and `postgres-integration.yml` remain `Planned`. | L | P3 | Build only when multi-node org deployment demands shared server storage; preserve SQLite as the zero-config local default. |
| **Native OS pointer actuator** | The six pointer tools run in the Python worker via opt-in `pip install "ltcai[pointer]"` (`pyautogui`). | L | P3 | Keep opt-in worker execution unless a cross-platform native Rust accessibility/input layer is prioritized. |
| **Quantized int8 vector backend efficiency** | Exhaustive int8 scanning is ~2.2× slower than `brute` at 10k rows without meaningful peak-RSS savings because fetched SQLite rows dominate memory. | L | P3 | Retain as a future disk-resident index representation; do not recommend over `brute` or `hnsw`. |

---

## Design Boundaries — Not Bugs To Close

These decisions are intentional product boundaries rather than open defects:

- **Telegram bridge & SSO/OIDC login callbacks:** Retired in `11.6.0` when the
  product server moved to Rust (`lattice-host`). Password login, sessions,
  rate limits, CSRF, and SSO configuration surfaces remain native in
  `lattice-auth`.
- **Local-export-only interop bridges:** Notion exports, local Git logs, and
  local `.eml`/`.ics` files avoid storing third-party OAuth tokens or running
  background vendor sync daemons.
- **`POST /knowledge-graph/ingest` stays text-only:** Binary documents belong on
  `/upload/document` → `/worker/parse`.
- **No bundled vision/speech models or `ffmpeg`:** Missing optional decoders
  honestly report `unavailable` instead of inflating install size or fabricating
  output.
- **Python `>= 90%` line-coverage floor vs. Frontend `100%`:** Intentional
  asymmetry documented in [`docs/DEVELOPMENT.md`](DEVELOPMENT.md).
- **Frozen parity goldens (`rust/fixtures/**/FROZEN.md`):** Preserved verbatim
  to guard historical byte compatibility; deliberate new behaviors live under
  new keys or new tests.

---

## How To Update This Ledger

Close a row only after the implementation and tests merge, then move it to
**Recently Closed** and record it in the corresponding release note. Do not edit
this file ahead of landed code.
