# Lattice AI v4.3.0 Product Hardening Report

## Summary

v4.3.0 turns the v4.2 Brain Core/storage release into a safer desktop product
without changing the Brain Core, storage, frontend, agent, or workflow
architecture.

## Desktop Shell

- Tauri sidecar startup now records command, PID, origin, running state, and
  last error.
- Tauri exposes `backend_status`, `restart_backend`, and `shutdown_backend`
  commands.
- Sidecar startup forces loopback host, disables Telegram, disables autoloaded
  models, disables network CORS, and disables tunnels.
- Missing Python or backend command failures are stored as actionable desktop
  status instead of being hidden.

## Backup And Restore

- `.latticebrain` archive export/inspect/verify/import/restore is API-backed.
- Backup health is exposed through FastAPI and the System settings view.
- Restore dry-run verifies the archive and returns planned targets without
  mutation.
- Restore/import requires explicit confirmation for destructive execution.
- SQLite-to-Postgres live migration now creates and verifies a pre-migration
  SQLite backup before copying data.

## Admin Status

`GET /admin/product-hardening` reports:

- local-only startup posture
- storage mode
- backup health
- public device identity metadata
- external integration opt-in state
- admin import/export/restore permissions
- fail-closed behavior for archive and restore errors

## Release Packaging

- Release artifact validation now checks the exact Tauri DMG path.
- Release artifact build script cleans only target-version outputs before
  rebuilding.
- Historical artifacts remain visible so wildcard upload mistakes from `dist`
  are still detectable.

## Registry Policy

v4.3.0 RC work builds and validates artifacts only. It does not publish to PyPI,
npm Registry, VS Code Marketplace, Open VSX, or any other external registry.
