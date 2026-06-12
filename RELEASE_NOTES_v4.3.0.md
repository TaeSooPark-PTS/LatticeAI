# Lattice AI v4.3.0 — Portability & Product Hardening

> Status: release candidate. This release candidate builds from `main` after
> v4.2.0. It does not tag, create a GitHub Release, publish packages, or deploy.

v4.3.0 hardens the v4.2 Digital Brain architecture into a safer portable
desktop product while preserving Brain Core, storage, frontend, agent/workflow,
API, and user-data compatibility.

## Highlights

- `.latticebrain` archives are now the primary portable brain format, with
  encrypted DB, blobs, workspace state, settings, workspace export bundles when
  present, storage metadata, provenance, and public device identity metadata.
- Archive inspect, verify, import, restore, and restore dry-run are exposed
  through real FastAPI routes and the System settings view.
- Destructive archive restore/import requires explicit admin confirmation.
- SQLite-to-Postgres live migration creates and verifies a pre-migration backup
  before copying data.
- Tauri sidecar startup, status, restart, shutdown, and local-only environment
  guards are hardened.
- Product-hardening admin status reports storage mode, backup health, device
  identity, import/export permissions, and external integration opt-in state.
- External integrations remain opt-in; token presence alone does not enable
  Telegram, connectors, model downloads, Docker, update checks, or peer sync.
- Release validation now includes exact-version wheel, sdist, npm tgz, VSIX,
  and Tauri DMG artifacts.

## Expected Artifacts

- `dist/ltcai-4.3.0-py3-none-any.whl`
- `dist/ltcai-4.3.0.tar.gz`
- `ltcai-4.3.0.tgz`
- `dist/ltcai-4.3.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.3.0_aarch64.dmg`

## Registry Policy

No external registries are published for this RC. PyPI, npm Registry, VS Code
Marketplace, Open VSX, and other external registries remain unpublished unless
the owner explicitly requests that after validation.
