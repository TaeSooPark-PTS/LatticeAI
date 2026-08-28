# CI & Release Gates

> Status: reference 2026-08-29

Reference for the GitHub Actions workflows that guard `main` and releases, the
supply-chain hardening applied to them, and the recommended branch-protection
configuration. Companion to [SECURITY_AUDIT.md](SECURITY_AUDIT.md) and
[BENCHMARKS.md](BENCHMARKS.md).

## Workflows

| Workflow | File | Triggers | Purpose |
|----------|------|----------|---------|
| CI | `.github/workflows/ci.yml` | push/PR to `main` | Lint, typecheck, unit + coverage + integration tests, product-readiness, build + wheel smoke, Rust workspace |
| Release | `.github/workflows/release.yml` | tags `v*` / `[0-9]*` | Build & validate artifacts (no publish; does not re-run CI lint/tests) |
| CD | `.github/workflows/publish.yml` | GitHub Release `published`, dispatch | Publish npm / PyPI / Marketplace / Open VSX (idempotent; never on a bare tag) |
| Visual Smoke | `.github/workflows/visual.yml` | push to `main`, nightly cron, dispatch | Playwright visual smoke (not on PRs) |
| Sidecar E2E | `.github/workflows/e2e-sidecar.yml` | nightly cron, dispatch | Live sidecar Playwright first-value loop |
| Dependency Audit | `.github/workflows/dependency-audit.yml` | weekly cron, dispatch | pip-audit + npm audit + CycloneDX SBOMs |
| Postgres Integration | `.github/workflows/postgres-integration.yml` | weekly cron, dispatch | Live SQLite→Postgres/pgvector migration test |

There is no `agent-smoke.yml`. Hosted runners have no MLX models; a fail-open
weekly smoke cannot report anything actionable.

Every workflow sets `concurrency.group` to `<workflow-file-stem>-${{ github.ref }}`
and cancels in-progress runs except on `main` (`cancel-in-progress: false` on
`release.yml`, which runs on tags). Job timeouts: test 40, integration 25,
build 20, rust 40, visual 15, sidecar 20, audit 15, release build 40,
postgres 20.

## CI matrix (`ci.yml`)

Four legs. Job names are `Test (Python <ver> on <os>)`:

| Leg | OpenAPI generate + upload | Full lint + typecheck + docs | Extension tests | Vitest | `pytest tests/unit/` | Coverage pytest (`tests/` + `--cov`) | Product readiness |
|-----|---------------------------|------------------------------|-----------------|--------|----------------------|--------------------------------------|-------------------|
| 3.11 + ubuntu-latest | yes | yes (`npm run lint`, `typecheck`, `docs:check-current`, `docs:check-links`) | yes (`test:browser-extension` && `test:vscode-extension`) | yes | yes | no | yes |
| 3.12 + ubuntu-latest | no | Python only (`lint:python`) | no | yes | yes | no | no |
| 3.14 + ubuntu-latest | no | Python only | no | yes | **no** (coverage invocation is the only pytest) | yes, line floor ≥90 | no |
| 3.11 + macos-latest | no | Python only | no | yes | yes | no | no |

Downstream jobs (after `test`): `Integration smoke test` (25 min), then
`Build package` (20 min). `Rust workspace` is independent (40 min).

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
(`@v4`) can be repointed by the action owner; a SHA cannot. SHAs were resolved
from the authoritative upstream refs via `git ls-remote --tags` on 2026-07-21.

| Action | Tag | Pinned commit SHA |
|--------|-----|-------------------|
| actions/checkout | v4.4.0 | `11d5960a326750d5838078e36cf38b85af677262` |
| actions/checkout | v5.1.0 | `fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09` |
| actions/setup-python | v5.6.0 | `a26af69be951a213d495a4c3e4e4022e16d87065` |
| actions/setup-python | v6.3.0 | `ece7cb06caefa5fff74198d8649806c4678c61a1` |
| actions/setup-node | v5.0.0 | `a0853c24544627f65ddf259abe73b1d18a591444` |
| actions/upload-artifact | v4.6.2 | `ea165f8d65b6e75b540449e92b4886f43607fa02` |

Pinning coverage: **100% of current `uses:` entries** across `ci.yml`,
`release.yml`, `visual.yml`, `e2e-sidecar.yml`, `dependency-audit.yml`, and
`postgres-integration.yml`. First-party `actions/*` plus pinned
`Swatinem/rust-cache` and `dtolnay/rust-toolchain` on the Rust/integration
legs. If a third-party action is added later, resolve and pin its SHA the same
way before merge.

## Dependency-audit gate posture

The audit workflow is **schedule + dispatch only** (not on push/PR). Both
`pip-audit --strict` and `npm audit --audit-level=high` fail the job on
findings. This is the loud channel for triage; a newly-disclosed CVE cannot
redden an unrelated change.

CycloneDX SBOMs (`sbom-python.json`, `sbom-npm.json`) are produced every run and
uploaded as artifacts. Regenerate locally with `scripts/generate_sbom.py`.

## Postgres integration gate

`postgres-integration.yml` still runs weekly + on demand, but the live
SQLite→Postgres writer it used to drive left in 11.6.0 with the Python
product write path. The suite file remains so a Monday cron is not a
missing-file failure; it skip-explains the retirement. SQLite is the live
Brain store. Restore a native pgvector door before making this job start
a container again.

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

- `Test (Python 3.11 on ubuntu-latest)`
- `Test (Python 3.12 on ubuntu-latest)`
- `Test (Python 3.14 on ubuntu-latest)`
- `Test (Python 3.11 on macos-latest)`
- `Integration smoke test`
- `Build package`
- `Rust workspace`

`Playwright visual smoke` is **not** a PR-blocking check: `visual.yml` no
longer has a `pull_request` trigger. It still runs on every push to `main`.

Advisory / non-blocking (schedule + dispatch only):

- `Python dependency audit (pip-audit)`
- `npm dependency audit`

Additional recommended settings if pull requests are used: require PRs before
merge, require branches to be up to date, require linear history, and dismiss
stale approvals on new commits.

## Release gate (publication safety)

`release.yml` builds and validates artifacts only — it never publishes. A tag
is only pushed on a commit that already passed CI, so the workflow does **not**
re-run `npm run lint`, `npm run typecheck`, `docs:check-links`, frontend tests,
or `pytest tests/unit/`.

It still runs:

- checkout / setup / installs
- `npm run docs:check-current` (release-specific current-version docs gate)
- `npm run build:assets`
- `python scripts/product_readiness.py` (safety net)
- `python -m build`, `wheel_smoke.py`, `twine check`
- `npm pack`, vsce package
- `validate_release_artifacts.py <v> --require-vsix --require-tgz`

Package publication (PyPI / npm / VS Code Marketplace / Open VSX) is
`publish.yml` (CD). It runs when a GitHub Release is **published**, or on
`workflow_dispatch` if that version already has a pushed `v*` tag. A bare
tag push never publishes. Local `npm run publish:*` still uses **exact
version filenames**, never a `dist/*` glob. See
[../RELEASE.md](../RELEASE.md) for the full procedure.

## Limitations (honest)

- Branch-protection rules are repository settings and cannot be committed from a
  workflow; the list above is the recommended configuration to apply in GitHub
  settings, not an enforced artifact.
- The audit gate depends on upstream advisory databases (OSV, npm registry);
  absence of findings means "nothing known", not "provably safe".
