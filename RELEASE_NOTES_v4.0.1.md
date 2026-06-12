# Lattice AI v4.0.1 — Digital Brain Platform Maintenance

> Status: GitHub Release only. This release builds and validates artifacts for
> commits on `main` after tag `v4.0.0`; it does not publish to PyPI, npm, the VS
> Code Marketplace, or Open VSX.

v4.0.1 is a maintenance release that closes the post-`v4.0.0` implementation
delta on `main` without reusing the `v4.0.0` version number.

## Included since v4.0.0

- Durable async run executor for agent/workflow runs: persisted queued/running/
  final states, realtime SSE progress, cooperative cancellation, and startup
  reconciliation of orphaned active runs.
- Stable user UUID migration, policy-backed admin authorization, local
  invitation tokens, and SQLite-backed Workspace OS state with JSON
  compatibility mirroring.
- Complete `/app` SPA parity and legacy UI retirement: token-native account,
  profile, password, workspace/org administration, invitations, snapshots/time
  machine with merge-restore, activity/presence, run approvals/cancellation,
  workflow trigger configuration/status, Brain Network pairing/push, chat
  context trace, and Knowledge Graph provenance coverage.
- en/ko i18n runtime coverage for routes, shell text, and parity views, guarded
  by frontend lint.

## Validated Artifacts

- `dist/ltcai-4.0.1-py3-none-any.whl`
- `dist/ltcai-4.0.1.tar.gz`
- `dist/ltcai-4.0.1.vsix`
- `ltcai-4.0.1.tgz`

The VSIX is the same validated package format used for VS Code-compatible and
Open VSX-compatible extension distribution. It is attached to the GitHub
Release only.

## Validation

- Python compile check, ruff, unit tests, and live integration tests passed.
- Frontend lint, VS Code extension typecheck, and Playwright visual tests
  passed.
- Release artifact validation, installed-wheel smoke, and npm pack dry-run
  passed.

## External Registries

No PyPI, npm Registry, VS Code Marketplace, or Open VSX publish step is part of
this release.
