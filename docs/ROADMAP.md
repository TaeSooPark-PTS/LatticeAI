[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./ROADMAP.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](./ko/ROADMAP.md)

# Lattice AI Gap Roadmap

> **Status: reference** — point-in-time gap inventory, refreshed at **v14.7.0**
> (2026-11-28). Not a canonical current-release document; version gates do not
> bind it.

As of **2026-11-28**, against the **14.7.0 — Private Scope, Verified Index** tree. This page
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

## Recently Closed (`v13.0.0` – `v14.7.0`) — Do Not Reopen

| Item | Shipped In | Resolution |
| --- | --- | --- |
| Private documents inside a workspace | `14.7.0` | Added owner-only `visibility` (`workspace` vs `private`) across `lattice-core` (`viewer_scope.rs`, `graph_write/`), `lattice-retrieval` (BM25F/vector/graph/ingest), `lattice-ingest` (`/upload/document`), `lattice-platform` (share & admin security dashboard), and Brain ingestion UI/API. |
| Default `LATTICEAI_VECTOR_INDEX` flip (`brute` → `hnsw`) | `14.7.0` | Verified `NativeHnswIndex` recall@10 against `datasets/rag_eval_v1.json` (`baseline=1.0000`, 5 churn rounds `0.9991..0.9963`) before flipping default from `brute` to `hnsw` across `lattice-retrieval`, `lattice-jobs`, and `lattice-platform`. |
| Hugging Face runtime commit `revision=` & SHA-256 verification | `14.7.0` | Completed revision pinning and SHA-256 digest verification (including LFS SHA-256) across `hf_revisions.py`, `local_models.py`, `embedding_providers/{autodetect,text}.py`, and `model_runtime/download.py` with clean unpinned override controls. |
| Small-model (0.5B–2B) multi-source synthesis quality | `14.7.0` | Added `synthesis.rs` structured `[S1]`..`[Sn]` evidence scaffolding, coverage refinement pass, echoed-instruction/stub rejection, and fail-closed `NEEDS_REVIEW` gating for decomposed profiles with zero per-model branches. |
| Optional LLM `refiner` for Self-Model extraction | `14.7.0` | Added opt-in `LATTICEAI_SELF_MODEL_LLM_REFINER=1` worker-backed refiner in `self_model_refiner.rs` alongside deterministic extraction, strictly proposal-first via Review Center. |
| Self-Model `open_keys` `snoozed` regression test | `14.7.0` | Added `desk_tests.rs` locking `desk::open_keys_in` suppression of duplicate re-proposals for `snoozed` items. |
| Dedicated Brain Network share UI | `14.7.0` | Added `brainNetwork.ts` and `BrainNetworkPanel.tsx` inside `BrainGovernanceHub` for device identity, peer pairing/push, and signed subgraph export/import into Review Center. |
| Owner-scoped automations & real trigger scheduler | `14.5.0` / `14.6.0` | Shipped live `interval`/`brain_event` triggers, provenance trust ranking, Obsidian Vault ZIP export, and owner-scoped `brain_event` default (`owner_scope: "self"`). |
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
| Zero-technical-debt pass (`ruff format`, zero `C901` ignores, fail-closed i18n ratchet, `Dockerfile` hygiene) | `14.1.1` | Formatted all 219 Python files (`ruff format .` + `.git-blame-ignore-revs` + CI `ruff format --check .`), decomposed `models.py`/`worker_compute.py` and removed all `"C901"` ignores in `pyproject.toml`, closed `check_server_i18n.mjs` to `0 unclaimed` via explicit `NON_ROUTER_HELPERS`, and added `--chown`/`--chmod` on `lattice-host` in `Dockerfile`. |

---

## P2 — Important Platform & Verification Gaps

| Item | Why It Remains | Difficulty | Priority | Suggested Path |
| --- | --- | --- | --- | --- |
| **Signed & notarized macOS DMG by default** | `.github/workflows/release.yml` supports Developer ID signing + notarization when `APPLE_CERTIFICATE` secrets are configured, but falls back to ad-hoc signing otherwise (requiring Gatekeeper approval on first launch). | M | **P2** | Configure repository Apple Developer ID + notarization secrets and extend `release:validate` with an optional `--require-signed` check. |
| **Live billed `api_key` completion contract test** | `api_key` mode live-probes `GET /models` with zero billing and fail-closes on errors, while live E2E chat completions run via zero-billing `cli_oauth` (`agy`/`grok`) and mock contracts. | S | **P2** | Add an opt-in scheduled/manual live completion contract smoke once a dedicated CI API budget is provisioned; keep `local_only` winning per request. |
| **Nightly real-model RAG eval runner** | `.github/workflows/nightly-rag-eval.yml` is wired and reports `skipped`/`unavailable` honestly when no model endpoint is configured, because standard hosted GitHub runners lack Apple Silicon MLX / local GPUs. | L | **P2** | Bind a self-hosted GPU/Apple Silicon runner or managed eval endpoint so nightly real-model scores publish automatically. |

---

## P3 — Honest Polish & Optional Extensions

| Item | Why It Remains | Difficulty | Priority | Suggested Path |
| --- | --- | --- | --- | --- |
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
