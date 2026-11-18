# CI & Release Gates

> Status: canonical (updated 2026-11-19) — Current release: **13.2.0**

Reference for the GitHub Actions workflows that guard `main` and releases, the
supply-chain hardening applied to them, and the recommended branch-protection
configuration. Companion to [SECURITY_AUDIT.md](SECURITY_AUDIT.md) and
[BENCHMARKS.md](BENCHMARKS.md).

## Workflows

| Workflow | File | Triggers | Purpose |
|----------|------|----------|---------|
| CI | `.github/workflows/ci.yml` | push/PR to `main` | Lint, typecheck, unit + coverage + retrieval sidecar/contract gates + integration tests, product-readiness, build + wheel smoke, Rust workspace |
| Release | `.github/workflows/release.yml` | tags `v*` / `[0-9]*` | Build & smoke-verify Linux assets + Apple Silicon macOS DMG, generate checksums, attach exact-version assets to the GitHub Release, and chain CD via `workflow_call` |
| CD | `.github/workflows/publish.yml` | GitHub Release `published`, `workflow_call`, dispatch | Publish npm / PyPI / Marketplace / Open VSX (idempotent) and verify/backfill `Lattice AI_<v>_aarch64.dmg` + checksums on the GitHub Release |
| Visual Smoke | `.github/workflows/visual.yml` | push to `main`, nightly cron, dispatch | Playwright visual smoke (not on PRs) |
| Sidecar E2E | `.github/workflows/e2e-sidecar.yml` | nightly cron, dispatch | Live sidecar Playwright first-value loop |
| Dependency Audit | `.github/workflows/dependency-audit.yml` | weekly cron, dispatch | pip-audit + npm audit + CycloneDX SBOMs |
| Postgres Integration | `.github/workflows/postgres-integration.yml` | weekly cron, dispatch | **Skipped / Planned** — retired Python SQLite→Postgres live suite guard (`SQLite` is the live default; PostgreSQL live migration is `Planned`) |

There is no `agent-smoke.yml`. Hosted runners have no MLX models; a fail-open
weekly smoke cannot report anything actionable.

Every workflow sets `concurrency.group` to `<workflow-file-stem>-${{ github.ref }}`
and cancels in-progress runs except on `main` (`cancel-in-progress: false` on
`release.yml`, which runs on tags). Job timeouts: test 40, integration 25,
build 20, rust 40, visual 15, sidecar 20, audit 15, release `build` 40,
release `macos-dmg` 45, release `github-release` 15, publish `publish` 30,
publish `macos-dmg` 45, publish `attach-release-assets` 15, postgres 20.

## CI matrix (`ci.yml`)

Four legs. Job names are `Test (Python <ver> on <os>)`:

| Leg | OpenAPI generate + upload | Full lint + typecheck + docs | Extension tests | Vitest | Retrieval sidecar (`hnswlib`) & contracts | `pytest tests/unit/` | Coverage pytest (`tests/` + `--cov`) | Product readiness |
|-----|---------------------------|------------------------------|-----------------|--------|-------------------------------------------|----------------------|--------------------------------------|-------------------|
| 3.11 + ubuntu-latest | yes | yes (`npm run lint`, `typecheck`, `docs:check-current`, `docs:check-links`) | yes (`test:browser-extension` && `test:vscode-extension`) | yes | yes (PR-blocking `.[ann]` + contract suite) | yes | no | yes |
| 3.12 + ubuntu-latest | no | Python only (`lint:python`) | no | yes | no | yes | no | no |
| 3.14 + ubuntu-latest | no | Python only | no | yes | no | **no** (coverage invocation is the only pytest) | yes, line floor ≥90 | no |
| 3.11 + macos-latest | no | Python only | no | yes | no | yes | no | no |

On `3.11 + ubuntu-latest`, CI installs `.[ann]` and verifies `import hnswlib`
before running the PR-blocking `Retrieval sidecar & contract gates` step
(`tests/unit/test_vector_index_sidecar.py`, `tests/unit/test_vector_index_hnsw.py`,
`tests/unit/test_cov_wp12_provider_contract.py`,
`tests/unit/test_worker_allowlist.py`, `tests/unit/test_hybrid_graph_bm25.py`,
`tests/unit/test_worker_compute.py`).

Downstream jobs (after `test`): `Integration smoke test` (25 min), then
`Build package` (20 min). `Rust workspace` is independent (40 min) and runs
explicit `cargo test -p lattice-retrieval --tests` (BM25F sidecar & GraphRAG
contracts), `cargo test --workspace`, and `cargo clippy --workspace --all-targets -- -D warnings`.

Removed from CI **and deleted from the tree** in 11.8.0 — a script that is not
a gate is a script that looks like one:

- `scripts/check_python.py` — ruff parses every file on every test leg
- `scripts/brain_quality_eval.py`
- `scripts/agent_eval.py`
- `scripts/bench_agent_smoke.py`

## Local lint chain (`npm run lint`)

Ten members, in order:

1. `lint:python` (ruff + mypy)
2. `lint:visual`
3. `lint:frontend`
4. `frontend:openapi:check`
5. `scripts/check_i18n_literals.mjs`
6. `check:i18n-namespaces`
7. `check:bundle`
8. `check:server-i18n`
9. `scripts/check_release_evidence_bound.mjs`
10. `check:max-file-lines`

Not in the lint chain (CI calls the extension tests directly on the 3.11+ubuntu
leg; the Python legacy-shim test is authoritative):

- `test:browser-extension` / `test:vscode-extension` (script entries remain)
- `check:legacy-debt` (script entry and `scripts/check_legacy_debt.mjs` both
  removed — the mjs mirror had drifted from the Python test)
- `check:python` (script entry and `scripts/check_python.py` both removed)

`npm run typecheck` is frontend-only (`typecheck:frontend`). The VS Code
extension is built by `test:vscode-extension` in CI and by `build_vsix.mjs`
at release.

## Coverage floor

Python: `[tool.coverage.report] fail_under = 90` (line coverage). Branch
measurement is off (`branch` is not set under `[tool.coverage.run]`). The
coverage step runs only on 3.14 + ubuntu-latest.

Frontend: `vitest.config.ts` still pins 100% thresholds on all four metrics;
that is unchanged and still runs on every CI test leg.

## Supply-chain hardening: pinned actions

Every third-party `uses:` in every workflow is pinned to an **immutable commit
SHA**, with the human-readable version in a trailing comment. A moving tag
(`@v7`) can be repointed by the action owner; a SHA cannot.

| Action | Tag / Ref | Pinned commit SHA |
|--------|-----------|-------------------|
| `actions/checkout` | `v7.0.1` | `3d3c42e5aac5ba805825da76410c181273ba90b1` |
| `actions/setup-python` | `v7.1.0` | `6d4fd22b20465ccc0f7354476588e8df7c9ca0e0` |
| `actions/setup-node` | `v7.1.0` | `949feb2413d6458794dcd2491c4babbbce0c15c1` |
| `actions/upload-artifact` | `v7.0.2` | `cf430e030ddbb5b0abf93d22962f4752f3646cd9` |
| `actions/download-artifact` | `v7.0.0` | `37930b1c2abaa49bbe596cd826c3c89aef350131` |
| `Swatinem/rust-cache` | `v2.9.3` | `d1527c8f67ef9af357290475d4af4b0fab8f12e0` |
| `dtolnay/rust-toolchain` | `stable` | `b725eda525362e7cf34af9a7243cac1e3f01a7ec` |

Pinning coverage: **100% of current `uses:` entries** across `ci.yml`,
`release.yml`, `publish.yml`, `visual.yml`, `e2e-sidecar.yml`,
`dependency-audit.yml`, and `postgres-integration.yml`. If a third-party action
is added later, resolve and pin its commit SHA the same way before merge.

## Dependency-audit gate posture

The audit workflow is **schedule + dispatch only** (not on push/PR). Both
`pip-audit --strict` and `npm audit --audit-level=high` fail the job on
findings. This is the loud channel for triage; a newly-disclosed CVE cannot
redden an unrelated change.

CycloneDX SBOMs (`sbom-python.json`, `sbom-npm.json`) are produced every run and
uploaded as artifacts. Regenerate locally with `scripts/generate_sbom.py`.

## Postgres integration gate (`Planned` / skipped)

**SQLite is the official live Brain store.** PostgreSQL/pgvector integration
remains **`Planned`** (config/dry-run planning APIs only; live write/migration
execution is not shipped in the Rust worker/host yet).

`postgres-integration.yml` runs weekly + on demand, but the live
SQLite→Postgres writer it used to drive left in 11.6.0 with the Python
product write path. The suite file remains so the weekly schedule is not a
missing-file failure; pytest explicitly **skips** and explains the retired
write path without starting a live Postgres service container. Restore a native
Rust/pgvector migration door before re-enabling a live DB container check.

## Dependency-audit clean run

`pip-audit --format markdown --output FILE` writes the file only when it
has findings. A clean run used to then fail on `cat FILE`. The workflow
now writes a placeholder report when the file is missing so a clean audit
is green.

## Visual smoke and sidecar E2E

This repository typically commits straight to `main`.

- `visual.yml` runs on push to `main`, the nightly cron, and dispatch. It does
  **not** run on pull requests.
- `e2e-sidecar.yml` is nightly + dispatch only (no push, no PR).

## Recommended branch protection for `main`

Required status checks (blocking). Names match the job `name:` fields:

- `Test (Python 3.11 on ubuntu-latest)` (includes PR-blocking retrieval sidecar/contract gates, OpenAPI check, lint, docs gates, and extension tests)
- `Test (Python 3.12 on ubuntu-latest)`
- `Test (Python 3.14 on ubuntu-latest)`
- `Test (Python 3.11 on macos-latest)`
- `Integration smoke test`
- `Build package`
- `Rust workspace` (includes `cargo test -p lattice-retrieval --tests`, workspace tests, and clippy)

`Playwright visual smoke` is **not** a PR-blocking check: `visual.yml` no
longer has a `pull_request` trigger. It still runs on every push to `main`.

Advisory / non-blocking (schedule + dispatch only):

- `Python dependency audit (pip-audit)`
- `npm dependency audit`

Additional recommended settings if pull requests are used: require PRs before
merge, require branches to be up to date, require linear history, and dismiss
stale approvals on new commits.

## Release gate (publication safety & macOS DMG assets)

`release.yml` builds, signs, smoke-verifies, and attaches exact-version assets
to the GitHub Release — it never publishes to package registries. A tag is only
pushed on a commit that already passed CI, so the workflow does **not** re-run
`npm run lint`, `npm run typecheck`, `docs:check-links`, frontend tests, or
`pytest tests/unit/`.

It runs two parallel build jobs and a final release upload job:

1. **`build` (`ubuntu-latest`)**:
   - `npm run docs:check-current`, `npm run build:assets`, `python scripts/product_readiness.py`
   - `python -m build`, `scripts/wheel_smoke.py`, `twine check`
   - `npm pack`, `scripts/build_vsix.mjs`
   - `validate_release_artifacts.py <v> --require-vsix --require-tgz`
   - Generates per-artifact `.sha256` files for wheel, sdist, npm tarball, and VSIX.
2. **`macos-dmg` (`macos-latest`)**:
   - Builds the frontend bundle (`npm run build:assets`), verifies `aarch64` Apple Silicon Tauri bundle (`npx tauri build --target aarch64-apple-darwin --bundles dmg`), producing `Lattice AI_<v>_aarch64.dmg`.
   - When `APPLE_CERTIFICATE` and `APPLE_CERTIFICATE_PASSWORD` secrets are configured, imports the Developer ID Application certificate into a temporary keychain and signs cleanly (with optional notarization when `APPLE_ID`, `APPLE_APP_PASSWORD`, and `APPLE_TEAM_ID` are set); otherwise applies ad-hoc signing (`codesign --force --deep --sign -`).
   - Runs `hdiutil verify`, mounts the DMG to verify arm64 Mach-O architecture and `codesign --verify`, runs `scripts/release_smoke.py`, validates via `validate_release_artifacts.py <v> --require-dmg`, and writes `<dmg>.sha256`.
3. **`github-release` (`ubuntu-latest`)**:
   - Downloads Linux and macOS artifacts, writes consolidated `SHA256SUMS-<v>.txt`, and creates/updates GitHub Release `v<v>` via `gh release create` / `gh release upload --clobber` with all exact-version deliverables (wheel, sdist, npm `.tgz`, `.vsix`, `_aarch64.dmg`) and checksums.

Package publication (PyPI / npm / VS Code Marketplace / Open VSX) is
`publish.yml` (`CD`). It runs when a GitHub Release is **published**, or on
`workflow_dispatch` if that version already has a pushed `v*` tag. A bare
tag push never publishes. `publish.yml` also checks whether `Lattice AI_<v>_aarch64.dmg`
already exists on the GitHub Release and builds/verifies/attaches it (plus
checksums) if missing. Local `npm run publish:*` still uses **exact version
filenames**, never a `dist/*` glob. See [../RELEASE.md](../RELEASE.md) for the
full procedure.

## Limitations (honest)

- Branch-protection rules are repository settings and cannot be committed from a
  workflow; the list above is the recommended configuration to apply in GitHub
  settings, not an enforced artifact.
- Hosted GitHub Actions macOS runners without `APPLE_CERTIFICATE` secrets produce
  ad-hoc signed DMGs; Developer ID signing and notarization execute automatically
  only when repository signing secrets are configured.
- The audit gate depends on upstream advisory databases (OSV, npm registry);
  absence of findings means "nothing known", not "provably safe".
