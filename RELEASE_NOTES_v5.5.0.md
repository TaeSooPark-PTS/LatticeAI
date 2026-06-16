# Lattice AI v5.5.0 — Release Coordination

**Date:** 2026-06-15  
**Branch:** main  
**Publish:** owner-run only; no package registry publish or deployment performed.

Lattice AI v5.5.0 completes the release coordination pass for the current
product line. It keeps the v5.4.0 Brain Automation Scheduler behavior as the
functional baseline and synchronizes all package, runtime, static, lockfile, and
release documentation references to 5.5.0.

## Highlights

- **Version sync**: Python package metadata, npm package metadata, VSIX metadata,
  Tauri metadata, runtime constants, lockfiles, and static asset manifest bumped
  to 5.5.0.
- **Release docs sync**: README, RELEASE.md, docs/CHANGELOG.md,
  FEATURE_STATUS.md, RELEASE_NOTES.md, vscode-extension/README.md, and this root
  release note described the 5.5.0 release-preparation line at that time.
- **Artifact exactness**: Release guidance uses exact 5.5.0 artifact filenames
  and continues to forbid wildcard publish commands such as `dist/*`.
- **Behavior preserved**: Consent-first Brain automation recipes, TriggerService
  dedup guards, LATTICE_TZ support, degraded status, enabled:false disarming,
  runtime graph cleanup, and E2E scenario coverage from v5.4.0 remain intact.

## Expected Artifacts (exact 5.5.0 names only)

- `dist/ltcai-5.5.0-py3-none-any.whl`
- `dist/ltcai-5.5.0.tar.gz`
- `dist/ltcai-5.5.0.vsix`
- `ltcai-5.5.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_5.5.0_aarch64.dmg`

## Tests & Gates

- Version consistency guard.
- Static release hygiene guard.
- Python validation.
- Python unit tests.
- Frontend lint/typecheck/build.
- VS Code extension build.
- Markdown link check.
- Python package build.

## Preserved

- Local-first defaults and explicit consent gates for downloads, cloud calls,
  Telegram, Brain Network, Docker/Postgres setup, update checks, and automation.
- Legacy trigger behavior for workflows without `enabled` field.
- Historical release notes and changelog entries for older versions.
- Package registry publishing remains owner-run only.

Full release guide: see [RELEASE.md](RELEASE.md).  
Changelog details: [docs/CHANGELOG.md](docs/CHANGELOG.md).  
Feature status: [FEATURE_STATUS.md](FEATURE_STATUS.md).
