[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./CI_AND_RELEASE_GATES.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](./ko/CI_AND_RELEASE_GATES.md)

# CI & Release Gates

> **Status: canonical** — updated 2026-11-25. Current release: **14.1.1 — Living Synthesis**.

Reference for the GitHub Actions workflows that guard `main` and releases, the
supply-chain hardening applied to them, and the recommended branch-protection
configuration. Companion to [`SECURITY_AUDIT.md`](SECURITY_AUDIT.md) and
[`BENCHMARKS.md`](BENCHMARKS.md).

## Workflows

| Workflow | File | Triggers | Purpose |
| --- | --- | --- | --- |
| CI | `.github/workflows/ci.yml` | push/PR to `main` | 10-job parallel gate: Frontend/i18n/docs/language-purity/extensions/100% Vitest, Python 3.11/3.12/3.14 Ubuntu + 3.11 macOS matrix, Integration smoke, Build/wheel smoke, split Ubuntu Rust `nextest` (`rust-core` + `rust-runtime`), Windows Rust `nextest` (`rust-windows`), macOS Tauri tests (`tauri-check`), digest-pinned Docker smoke (`docker-smoke`), and PR + `main` Playwright Visual Smoke (`visual`) |
| Release | `.github/workflows/release.yml` | `workflow_run` (`CI` completed on `main`), tags `v*` / `[0-9]*`, dispatch | Hard-enforces completed green `CI` on exact commit SHA, supports `[skip release]` opt-out, verifies full multi-manifest version consistency (`bump_version.py --check`) + docs/language gates before auto-tagging unreleased versions on `main`, builds & smoke-verifies core assets + Apple Silicon macOS DMG, attaches GitHub Release assets, and chains CD via `workflow_call` |
| CD | `.github/workflows/publish.yml` | `workflow_call`, GitHub Release `published`, dispatch | Independently re-enforces green `CI` on exact commit SHA, reuses checksum-verified GitHub Release assets (zero duplicate build), publishes npm / PyPI / Marketplace / Open VSX (idempotent), and skips macOS runner allocation when DMG already exists |
| Visual Smoke | `.github/workflows/visual.yml` | nightly cron, dispatch | Scheduled / on-demand Playwright visual smoke with cached Chromium and fast-path shared-lib check (PR and `main` push visual smoke run inside `ci.yml`) |
| Sidecar E2E | `.github/workflows/e2e-sidecar.yml` | tags, relevant PR paths, nightly cron, dispatch | Live sidecar Playwright first-value loop with 15s APT mirror failover |
| Nightly RAG Eval | `.github/workflows/nightly-rag-eval.yml` | nightly cron, dispatch | Real-model RAG evaluation against `datasets/rag_eval_v1.json` with claim-level grounding & fabricated-citation gates; reports `skipped`/`unavailable` honestly when endpoints are unset |
| Dependency Audit | `.github/workflows/dependency-audit.yml` | tags, weekly cron, dispatch | `pip-audit` + `npm audit` + CycloneDX SBOMs |
| Postgres Integration | `.github/workflows/postgres-integration.yml` | dispatch only | **Skipped / Planned** — retired Python SQLite-to-Postgres live suite guard (`SQLite` is the live default; PostgreSQL live migration is `Planned`) |

Every workflow sets `concurrency.group` to `<workflow-file-stem>-${{ github.ref }}`
and cancels in-progress runs except on `main` (`cancel-in-progress: false` on
`release.yml`). `ci.yml` sets top-level `permissions: contents: read` by default.

## CI Job Architecture (`ci.yml`)

### 1. Single-Run Frontend, Extensions & Docs (`frontend`)
Runs once on `ubuntu-latest` (instead of duplicating across every Python matrix leg):
- `npm run lint:visual`, `npm run lint:frontend`, `npm run typecheck` (TypeScript 7.0.2 with `"noUncheckedIndexedAccess": true`)
- `node scripts/check_i18n_literals.mjs`, `npm run check:i18n-namespaces`, `npm run check:bundle`, `npm run check:server-i18n`
- `node scripts/check_release_evidence_bound.mjs`, `npm run check:max-file-lines`
- `npm run docs:check-current`, `npm run docs:check-links`, `npm run docs:check-languages`
- `node scripts/check_ubuntu_runner_compat.mjs --diagnostic`
- `npm run test:browser-extension && npm run test:vscode-extension`
- `npm run test:frontend:coverage` (`vitest.config.mts` pins 100% coverage on statements, branches, functions, and lines across 113 test files / 1,852 tests)

### 2. Python Matrix (`test`)
Four legs (`Test (Python <ver> on <os>)`):

| Leg | OpenAPI sync | Python lint (`ruff` + `mypy`) | Retrieval baseline (`hnswlib`) | Unit tests | Coverage floor (>=90%) | Product readiness |
| --- | --- | --- | --- | --- | --- | --- |
| 3.11 + ubuntu-latest | yes | yes (`ruff format --check .`, `BLE`, global McCabe <= 20 with zero `C901` ignores, Bandit `S608` + minimal ignores) | yes (`.[ann]`) | `pytest tests/unit/` | no | yes |
| 3.12 + ubuntu-latest | no | no | no | `pytest tests/unit/` | no | no |
| 3.14 + ubuntu-latest | no | no | no | via `--cov` invocation | yes (`fail_under = 90`) | no |
| 3.11 + macos-latest | no | no | no | `pytest tests/unit/` | no | no |

### 3. Parallel Downstream Jobs (`integration` & `build`)
After `frontend` and `test` pass, both run in parallel:
- **`Integration smoke test` (`integration`)**: boots `lattice-host` supervising the Python worker and runs `npm run test:integration`.
- **`Build package` (`build`)**: builds wheel + sdist (`python -m build`), checks metadata with `twine check`, and executes `scripts/wheel_smoke.py` in a fresh isolated venv.

### 4. Split Parallel Ubuntu Rust Workspace (`rust-core` & `rust-runtime`)
Both jobs use `Swatinem/rust-cache` and pinned `taiki-e/install-action` for **`cargo-nextest`**:
- **`Rust (lint & core crates)` (`rust-core`)**: `cargo fmt --all -- --check`, `cargo clippy --workspace --all-targets -- -D warnings`, `cargo nextest run` + `cargo test --doc` for `lattice-core`, `lattice-retrieval`, `lattice-ingest`, `lattice-jobs`, and `lattice-auth`.
- **`Rust (agent, platform, chat & host)` (`rust-runtime`)**: `cargo nextest run` + `cargo test --doc` for `lattice-agent`, `lattice-platform`, `lattice-chat`, and `lattice-host`.

### 5. Cross-Platform Rust, Desktop & Container Jobs (`rust-windows`, `tauri-check`, `docker-smoke`)
- **`Rust (Windows workspace tests)` (`rust-windows`)**: runs `cargo nextest run --workspace --no-fail-fast` and `cargo test --doc --workspace` on `windows-latest`, exercising Windows handle-pinned `safe_io.rs` and path semantics.
- **`Desktop shell (Tauri check & tests)` (`tauri-check`)**: runs `cargo test --manifest-path src-tauri/Cargo.toml` on `macos-latest`, verifying `PortPolicy::PinnedProduction`, config/capability parity, and gateway lifecycle state.
- **`Docker image build smoke` (`docker-smoke`)**: builds the SHA256 digest-pinned multi-stage `Dockerfile` and runs `docker run --rm --entrypoint lattice-host latticeai:ci-smoke --version`.

### 6. Fast-Path Playwright Visual Smoke (`visual`, PR & `main`)
- Runs on **both pull requests and pushes to `main`** (`ubuntu-24.04`) so visual regressions are blocked before merge.
- Probes `ldconfig -p` for all 8 Chromium shared libraries first; skips `apt-get` completely when pre-installed on `ubuntu-24.04` (avoiding unnecessary Tauri/WebKit packages).
- Caches `~/.cache/ms-playwright` keyed on `package-lock.json` via `actions/cache@v6.1.0` and serves the committed `static/app/` bundle verified by `check:bundle`.

## Supply-Chain Hardening: Pinned Actions

Every third-party `uses:` across all workflows is pinned to an **immutable commit SHA**:

| Action | Tag / Ref | Pinned commit SHA |
| --- | --- | --- |
| `actions/checkout` | `v7.0.1` | `3d3c42e5aac5ba805825da76410c181273ba90b1` |
| `actions/setup-python` | `v7.1.0` | `6d4fd22b20465ccc0f7354476588e8df7c9ca0e0` |
| `actions/setup-node` | `v7.1.0` | `949feb2413d6458794dcd2491c4babbbce0c15c1` |
| `actions/cache` | `v6.1.0` | `55cc8345863c7cc4c66a329aec7e433d2d1c52a9` |
| `actions/upload-artifact` | `v7.0.2` | `cf430e030ddbb5b0abf93d22962f4752f3646cd9` |
| `actions/download-artifact` | `v7.0.0` | `37930b1c2abaa49bbe596cd826c3c89aef350131` |
| `Swatinem/rust-cache` | `v2.9.3` | `d1527c8f67ef9af357290475d4af4b0fab8f12e0` |
| `dtolnay/rust-toolchain` | `stable` | `b725eda525362e7cf34af9a7243cac1e3f01a7ec` |
| `taiki-e/install-action` | `v2.90.8` | `fe3acb53a98b62c772db35de75ce0e4595e7ca8c` |

## Hard CI-to-CD Enforcement & Release Safety (`release.yml` -> `publish.yml`)

1. **Strict fail-closed CI verification:**
   - Both `release.yml` (`verify-ci`) and `publish.yml` (`publish`) query the GitHub Actions API for `ci.yml` runs matching `git rev-parse HEAD` with `status=completed` and `conclusion == "success"`.
   - If `CI` failed or never completed cleanly on that exact SHA, Release and CD exit non-zero and **never deploy**.
2. **Automatic post-CI release & premature-bump protection:**
   - When `CI` completes with `success` on a `main` push, `release.yml` fires automatically via `workflow_run`.
   - If the commit message contains `[skip release]` or `[no release]`, auto-release is cleanly skipped (`should_release=false`).
   - If `v<version>` already exists on `origin`, ordinary `main` commits are cleanly skipped.
   - Before auto-creating and pushing tag `v<version>`, `verify-ci` runs:
     - `python3 scripts/bump_version.py <version> --check` (every manifest across Python, npm, Cargo, Tauri, OpenAPI, and VS Code must match),
     - `node scripts/check_current_release_docs.mjs` (release docs, changelog, and release evidence must match), and
     - `node scripts/check_doc_languages.mjs` (bilingual language purity and 100% EN↔KO parity must pass).
3. **Zero-duplicate CD (`publish.yml`):**
   - `release.yml` chains `publish.yml` via `workflow_call` immediately after publishing the GitHub Release and attaching exact-version assets + `SHA256SUMS-<version>.txt`.
   - `publish.yml` downloads and SHA-256 verifies the core assets directly from GitHub Release, publishes to PyPI, npm, VS Code Marketplace, and Open VSX idempotently, and skips macOS runner allocation when `Lattice AI_<version>_aarch64.dmg` is already attached.

See [`../RELEASE.md`](../RELEASE.md) for the release checklist and manual fallback commands.

## Recommended Branch Protection for `main`

Required status checks (blocking):
- `Frontend, extensions & docs`
- `Test (Python 3.11 on ubuntu-latest)`
- `Test (Python 3.12 on ubuntu-latest)`
- `Test (Python 3.14 on ubuntu-latest)`
- `Test (Python 3.11 on macos-latest)`
- `Integration smoke test`
- `Build package`
- `Rust (lint & core crates)`
- `Rust (agent, platform, chat & host)`
- `Rust (Windows workspace tests)`
- `Desktop shell (Tauri check & tests)`
- `Docker image build smoke`
- `Playwright visual smoke`

Advisory / scheduled checks:
- `Python dependency audit (pip-audit)`
- `npm dependency audit`
- `Nightly Real-Model RAG Eval`

## Honest Limitations

- Branch-protection rules live in GitHub repository settings rather than workflow files.
- Hosted GitHub Actions macOS runners without `APPLE_CERTIFICATE` secrets produce ad-hoc signed DMGs; Developer ID signing and notarization execute automatically only when repository signing secrets are configured.
- Dependency audits rely on upstream advisory databases (OSV, npm registry); zero findings means no known advisories rather than formal proof of absence.
