# Lattice AI v4.7.1 - Admin Operations Release

Lattice AI v4.7.1 keeps the product promise focused: the user opens a private
Living Brain, not a database dashboard. Operational needs now live in a separate
Admin Console so the main Brain surface can stay simple while administrators can
still inspect users, logs, security events, policies, and Brain health.

## Why 4.7.1

- The product direction now clearly separates everyday users from operators.
- User-facing Brain chat remains the home screen.
- Admin and observability workflows are available without turning the user page
  into a control panel.
- `4.7.1` is the exact version for package metadata, release artifacts, Git tag,
  GitHub Release assets, README links, and owner publishing commands.

## Highlights

- Added role permission visibility to the dedicated `#/admin` Admin Console.
- Added audit log search and severity filtering backed by `/admin/audit` query
  parameters instead of client-only filtering.
- Added `/admin/log-retention` so operators can see local retention posture,
  retained event counts, and export-before-prune status.
- Split Admin Console data loading into a dedicated frontend hook so Brain user
  state and admin observability state do not share UI runtime state.
- Preserved workspace-scoped admin history, audit, stats, and sensitivity reads
  when the client sends `X-Workspace-Id` or `workspace_id`.
- Updated visual tests to enforce that the admin console is separated from the
  normal user Brain surface.
- Refreshed README, architecture, changelog, release guide, security posture,
  feature status, VS Code extension docs, and release evidence paths for v4.7.1.

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

- `dist/ltcai-4.7.1-py3-none-any.whl`
- `dist/ltcai-4.7.1.tar.gz`
- `dist/ltcai-4.7.1.vsix`
- `ltcai-4.7.1.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.7.1_aarch64.dmg`

## Evidence

- README screenshots and GIF:
  [output/release/v4.7.1/SCREENSHOT_INDEX.md](output/release/v4.7.1/SCREENSHOT_INDEX.md)
- Admin operations report:
  [docs/V4_7_1_ADMIN_OPERATIONS_REPORT.md](docs/V4_7_1_ADMIN_OPERATIONS_REPORT.md)

## Publishing Scope

This release prepares GitHub Release assets and owner publishing commands for
npm, PyPI, VS Code Marketplace, and Open VSX using exact v4.7.1 filenames.
