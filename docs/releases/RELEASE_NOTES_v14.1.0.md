[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./RELEASE_NOTES_v14.1.0.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](../ko/releases/RELEASE_NOTES_v14.1.0.md)

# Lattice AI v14.1.0 — Living Synthesis (2026-11-25)

## Highlights

### 1. Critical v14.0.0 Regression Fixes
- **Unified Rust ↔ Python UTC Timestamps (`rust/lattice-core/src/graph_write/clock.rs`, `lattice_brain/utils.py`, `lattice_brain/graph/_kg_fsutil.py`):**
  - Eliminated the 9-hour timezone skew on non-UTC hosts (such as UTC+9) by unifying Python's `local_now()`, `now_iso()`, `_recency_score()`, and `_safe_iso_from_stat_mtime()` onto offset-free UTC (`YYYY-MM-DDTHH:MM:SS`) matching Rust's pure-Rust `SystemClock::now_iso()` (`gmtoff: 0`).
  - Updated contradictory pre-v14.0.0 comments across `clock.rs`, `workspace/pyutil.rs`, `retrieval/routes/mod.rs`, `chat/turn.rs`, and `command_center_api/store.rs`, and added Rust and Python regression tests (`tests/unit/test_timestamp_utc_consistency.py`) verifying zero skew under `Asia/Seoul` (UTC+9), `America/New_York` (UTC-5), and `UTC`.
- **Tauri Production Pinned-Port Policy (`src-tauri/src/topology.rs`, `src-tauri/src/backend.rs`):**
  - Introduced explicit `PortPolicy` (`PinnedProduction` vs. `AllowDynamicFallback`), pinning production builds strictly to `PINNED_PRODUCTION_PORT` (`4825`) so the runtime gateway origin never diverges from `tauri.conf.json` CSP and `capabilities/default.json` remote IPC URLs.
  - Replaced silent upward fallback scanning (`4826+`) in production with fail-fast conflict validation and clear, actionable user-facing `gateway_error` diagnostics, backed by unit tests verifying policy enforcement and config/capability parity.

### 2. Full-Stack Technical Debt & Strictness Hardening
- **Narrowed Python Exception Handling & Tightened Ruff Rules (`pyproject.toml`, `lattice_brain/`, `latticeai/`, `scripts/`, `tests/`):**
  - Replaced all 6 broad `except Exception` / `# noqa: BLE001` blocks in `lattice_brain/multimodal/images.py`, all 6 blocks in `latticeai/models/router/loading.py`, and all remaining blind-exception catches across the repository with concrete exception tuples.
  - Enabled `"BLE"` globally in `[tool.ruff.lint] select`, lowered `[tool.ruff.lint.mccabe] max-complexity` from `30` to `20` (refactoring all 12 functions exceeding 20 into cohesive helpers), and trimmed `[tool.ruff.lint.per-file-ignores]` (`S603`, `S105`, `S108`) to the minimum required files while keeping `S608` active on production code.
- **Rust Auth, Tauri Command Signatures & Docstring Cleanup:**
  - Moved `CSRF_DENIED_DETAIL` and `CSRF_ERROR_CODE` into `rust/lattice-auth/src/messages.rs` and replaced raw reason strings in `rust/lattice-auth/src/csrf.rs` with the structured `CsrfReason` enum (`as_str()` preserves byte-identical HTTP 403 JSON contracts).
  - Updated infallible Tauri commands `backend_status` and `shutdown_backend` in `src-tauri/src/main.rs` to return `BackendStatus` directly via `tauri::AppHandle` instead of vacuous `Result<BackendStatus, String>` wrappers.
  - Updated `lattice_brain/quiet.py` docstring to accurately state that `lattice_brain.quiet` is the single source of truth for suppressed-exception logging.
- **TypeScript 7 `noUncheckedIndexedAccess` (`tsconfig.json`, `frontend/src/`):**
  - Enabled `"noUncheckedIndexedAccess": true` and resolved all 457 resulting TypeScript errors across 68 frontend source and test files while preserving **100% Vitest coverage** across statements, branches, functions, and lines (`113` files, `1,852` tests).
- **PR Visual Regression Gate (`.github/workflows/ci.yml`):**
  - Enabled the Playwright `visual` smoke job on pull requests as well as pushes to `main` so visual regressions are caught before merge.

### 3. Documentation Modernization (`v14.1.0`)
- Refreshed all canonical and reference Markdown documentation across the repository root, `docs/`, and Korean mirrors (`README.ko.md`, `docs/ko/`) to `v14.1.0`, pruning superseded pre-v13 narrative blocks and stale `v12.x` roadmap entries while preserving 100% EN↔KO bidirectional parity.
