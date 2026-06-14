# Lattice AI v4.7.0 - Admin Separation Release

Lattice AI v4.7.0 keeps the product promise focused: the user opens a private
Living Brain, not a database dashboard. Operational needs now live in a separate
Admin Console so the main Brain surface can stay simple while administrators can
still inspect users, logs, security events, policies, and Brain health.

## Why 4.7.0

- The product direction now clearly separates everyday users from operators.
- User-facing Brain chat remains the home screen.
- Admin and observability workflows are available without turning the user page
  into a control panel.
- `4.7.0` is the exact version for package metadata, release artifacts, Git tag,
  GitHub Release assets, README links, and owner publishing commands.

## Highlights

- Added a dedicated `#/admin` Admin Console with a return path to the Brain.
- Added admin overview metrics for users, recent logs, security status, and
  Brain index status.
- Added user directory, audit log, security event, policy, and Brain operations
  sections in the admin-only surface.
- Scoped admin history, audit, stats, and sensitivity reads by workspace when
  the client sends `X-Workspace-Id` or `workspace_id`.
- Added frontend API coverage for `/admin/stats` and `/admin/security/events`.
- Updated visual tests to enforce that the admin console is separated from the
  normal user Brain surface.
- Refreshed README, architecture, changelog, release guide, security posture,
  feature status, VS Code extension docs, and release evidence paths for v4.7.0.

## Preserved

- Living Brain remains the primary user experience.
- The Knowledge Graph remains the deepest Brain exploration layer, not the home.
- Local-first ownership, privacy-first defaults, encrypted `.latticebrain`
  archives, backup/restore, and rollback-safe restore behavior remain intact.
- Brain Core, `lattice_brain` isolation, FastAPI APIs, Tauri shell,
  StorageEngine, SQLite default, PostgreSQL/pgvector opt-in, model runtime,
  agent runtime, workflow runtime, graph/search/chat/capture/automation/system
  capabilities are preserved.

## Expected Artifacts

- `dist/ltcai-4.7.0-py3-none-any.whl`
- `dist/ltcai-4.7.0.tar.gz`
- `dist/ltcai-4.7.0.vsix`
- `ltcai-4.7.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.7.0_aarch64.dmg`

## Evidence

- README screenshots and GIF:
  [output/release/v4.7.0/SCREENSHOT_INDEX.md](output/release/v4.7.0/SCREENSHOT_INDEX.md)
- Admin separation report:
  [docs/V4_7_0_ADMIN_SEPARATION_REPORT.md](docs/V4_7_0_ADMIN_SEPARATION_REPORT.md)

## Publishing Scope

This release prepares GitHub Release assets and owner publishing commands for
npm, PyPI, VS Code Marketplace, and Open VSX using exact v4.7.0 filenames.
