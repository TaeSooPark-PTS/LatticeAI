[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./RELEASE_NOTES_v14.0.0.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](../ko/releases/RELEASE_NOTES_v14.0.0.md)

# Lattice AI v14.0.0 — Living Synthesis (2026-11-25)

## Highlights

### 1. Rust Host, Auth & Sub-Crate Hardening
- **Versioned `$scrypt$` Password Hashing (`rust/lattice-auth/src/password.rs`):** Replaced panicking `.expect()` calls with `Result<String, PasswordError>`, upgraded newly hashed passwords to versioned `$scrypt$n=14,r=8,p=1${salt}:{key}` while maintaining backward verification for legacy `{salt}:{key}` hashes, and tightened `stored_is_hashed` validation.
- **Bounded Rate Limiter & Unified HTTP `429` Contract (`rust/lattice-auth/src/{ratelimit.rs,csrf.rs}`):** Capped IP/bucket maps at `10,000` entries with expired-first and oldest-entry eviction, added mutex poison recovery, unified `429` JSON + `Retry-After` responses across `check_ip` and `enforce`, and standardized the CSRF rejection message to English.
- **Zero-`libc` Pure-Rust UTC Clock (`rust/lattice-core/src/graph_write/clock.rs`):** Eliminated all `unsafe` `libc::localtime_r`/`gmtime_r`/`mktime` FFI calls via pure-Rust civil-calendar arithmetic and removed the `libc` dependency from `lattice-core`, ensuring identical behavior on macOS, Linux, and Windows.
- **Non-Blocking Async I/O, Bounded Caches & Fail-Closed Gateway:**
  - Offloaded blocking `std::fs` calls in `lattice-ingest/src/local_files_api/local.rs` and session persistence in `lattice-chat/src/documents.rs` to `tokio::task::spawn_blocking` (releasing the session mutex before disk writes).
  - Bounded `lattice-chat/src/cloud/budget.rs` (`4,096` scopes) and `lattice-retrieval/src/native_hnsw.rs` (`32` graphs) with LRU eviction and poison recovery.
  - Preserved partial drain progress in `lattice-jobs`, surfaced claim-release errors, and added a loopback/Bearer defense-in-depth guard on `/host/jobs`.
  - Made `GatewayState::with_client` fail closed when building the no-redirect proxy client fails, and replaced `std::env::set_var` in `lattice-host` with `EnvOverlayProbe`.

### 2. Python AI Worker, Brain Core & Packaging
- **Platform-Safe Packaging (`pyproject.toml`):** Guarded `mlx-vlm`/`mlx-lm` in `local`/`all` extras with `; sys_platform == 'darwin' and platform_machine == 'arm64'`, added `hnswlib`/`pyautogui` to `all`, removed empty extras, pointed the Changelog URL to root `CHANGELOG.md`, and re-enabled Ruff security checks (`S608`, `S105`, `S108`, `S603`, `S604`, `S607`).
- **Worker & Brain Resilience:** Added `ContextVar`-scoped `llm_router_scope` DI in `lattice_brain/graph/runtime.py`, safe `LATTICEAI_VECTOR_DIM` parsing + pre-compiled regexes + `embed_async` in `lattice_brain/embeddings.py`, shared `record_suppressed` deduplication in `lattice_brain/quiet.py`, bounded rate buckets and invalid-CIDR warnings in `latticeai/core/security.py`, read-only `todo_read` without directory side effects, and narrowed exception handlers.

### 3. Strict TypeScript 7 Frontend, Tauri Desktop & Extensions
- **Frontend (`frontend/src/`, `tsconfig.json`, `package.json`):** Enabled `noImplicitReturns`, `noFallthroughCasesInSwitch`, `noUnusedLocals`, `noUnusedParameters`, and `verbatimModuleSyntax`; extracted modular `AppRouteView.tsx`; moved runtime UI packages into `dependencies`; cleared failed desktop origin cache for transient retry; added SSR/non-DOM safe storage guards; and maintained **100% Vitest coverage** across 113 test files.
- **Tauri 2.11 Desktop (`src-tauri/`):** Serialized async gateway `start`/`stop`/`restart` lifecycle without TOCTOU races or `std::env::set_var`, enforced loopback-only external origin validation and safe port wrapping, moved `/usr/bin/osascript` folder picking into `spawn_blocking`, and pinned desktop capabilities & CSP to `127.0.0.1:4825` without `'unsafe-inline'`.
- **VS Code & Browser Extensions:** Added optional Bearer auth, TTL single-use approval token caching, stream timeouts, live config reload, and cross-platform VSIX packaging (`scripts/vsix-cli.mjs`) to `vscode-extension`; narrowed `browser-extension` host permissions to port `4825`, switched capture to live DOM visibility walking, and enforced UTF-8 byte truncation and end-to-end fetch timeouts.

### 4. Cross-Platform CI/CD, Containerization & Architecture Docs
- **Real Windows Rust CI, Tauri CI & Docker Smoke (`.github/workflows/ci.yml`, `Dockerfile`, `.dockerignore`):** Added `rust-windows` (`runs-on: windows-latest`), `tauri-check` (`runs-on: macos-latest`), and `docker-smoke` jobs; pinned `taiki-e/install-action` and all multi-stage `Dockerfile` base images (`node:22-slim`, `rust:1.100-slim-bookworm`, `python:3.11-slim-bookworm`) by SHA256 digest; added top-level `permissions: contents: read`; and included frontend builds cleanly inside Docker.
- **Modernized 14.0.0 Architecture Docs:** Brought `ARCHITECTURE.md`, `docs/ko/ARCHITECTURE.md`, `docs/architecture.md`, `docs/ko/architecture-overview.md`, and crate `ARCHITECTURE.md` maps up to `14.0.0`, eliminating outdated pre-14 dead-module history and stale compatibility alias notes.
