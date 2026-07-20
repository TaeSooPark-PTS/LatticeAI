# CI & Release Gates

> Status: reference 2026-07-21

Reference for the GitHub Actions workflows that guard `main` and releases, the
supply-chain hardening applied to them, and the recommended branch-protection
configuration. Companion to [SECURITY_AUDIT.md](SECURITY_AUDIT.md) and
[BENCHMARKS.md](BENCHMARKS.md).

## Workflows

| Workflow | File | Triggers | Purpose |
|----------|------|----------|---------|
| CI | `.github/workflows/ci.yml` | push/PR to `main` | Lint, typecheck, unit + integration tests, brain/agent eval gates, build + wheel smoke |
| Release | `.github/workflows/release.yml` | tags `v*` / `[0-9]*` | Build & validate all artifacts (no publish) |
| Visual Smoke | `.github/workflows/visual.yml` | push/PR, nightly cron, dispatch | Playwright visual smoke |
| **Dependency Audit** | `.github/workflows/dependency-audit.yml` | push/PR, weekly cron, dispatch | pip-audit + npm audit + CycloneDX SBOMs |
| **Postgres Integration** | `.github/workflows/postgres-integration.yml` | weekly cron, dispatch | Live SQLite→Postgres/pgvector migration test |

The last two were added for the P2 deployment-trust / supply-chain workstream.

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
`release.yml`, `visual.yml`, `dependency-audit.yml`, and
`postgres-integration.yml`. All are first-party `actions/*`. If a third-party
action is added later, resolve and pin its SHA the same way before merge.

## Dependency-audit gate posture (conservative)

The audit workflow is intentionally **non-blocking on day-to-day PRs** and
**strict on the scheduled/dispatch run**:

- `push` / `pull_request`: `pip-audit` and `npm audit` run with
  `continue-on-error` keyed on the event name, so a newly-disclosed CVE in a
  transitive dependency never reddens an unrelated PR. Findings still appear in
  the job summary and uploaded report artifacts.
- `schedule` (weekly) / `workflow_dispatch`: the same steps run **strict** —
  `pip-audit --strict` fails on any known vuln; `npm audit --audit-level=high`
  fails on high/critical. This is the loud channel for triage.

CycloneDX SBOMs (`sbom-python.json`, `sbom-npm.json`) are produced every run and
uploaded as artifacts. Regenerate locally with `scripts/generate_sbom.py`.

**To promote to a required blocking PR check:** remove the `continue-on-error`
expressions in `dependency-audit.yml` and add `python-audit` / `npm-audit` to
the required-checks list below.

## Postgres integration gate

`postgres-integration.yml` runs the migration-integrity suite
(`tests/integration/test_v42_postgres_migration_live.py`) that is skipped
everywhere else. The suite is gated by `LTCAI_LIVE_POSTGRES_DOCKER_CONSENT=1`;
the workflow sets that variable, and the test itself starts and tears down a
`pgvector/pgvector:pg16` container via `docker compose` (preinstalled on
GitHub-hosted `ubuntu-latest`). It is deliberately kept off the PR path (slow,
image pull, needs Docker) and runs weekly + on demand.

## Recommended branch protection for `main`

Required status checks (blocking):

- `CI / Test (Python 3.11)`
- `CI / Test (Python 3.12)`
- `CI / Integration smoke test`
- `CI / Build package`
- `Visual Smoke / Playwright visual smoke`

Advisory / non-blocking today (promote when the team is ready):

- `Dependency Audit / Python dependency audit (pip-audit)`
- `Dependency Audit / npm dependency audit`

Additional recommended settings: require PRs before merge, require branches to
be up to date, require linear history, and dismiss stale approvals on new
commits.

## Release gate (publication safety)

`release.yml` builds and validates artifacts only — it never publishes. Package
publication (PyPI / npm / VS Code Marketplace / Open VSX) is a manual step
(`npm run publish:*`) using **exact version filenames**, never a `dist/*` glob,
so pushing a tag can never publish by accident. See
[../RELEASE.md](../RELEASE.md) for the full procedure.

## Limitations (honest)

- Branch-protection rules are repository settings and cannot be committed from a
  workflow; the list above is the recommended configuration to apply in GitHub
  settings, not an enforced artifact.
- The audit gate depends on upstream advisory databases (OSV, npm registry);
  absence of findings means "nothing known", not "provably safe".
