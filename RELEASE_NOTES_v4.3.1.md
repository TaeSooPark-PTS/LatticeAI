# Lattice AI v4.3.1 — End-User Audit Repair RC

> Status: release candidate repair build. This build does not tag, create a
> GitHub Release, publish packages, or deploy.

v4.3.1 fixes the P0/P1 blockers found during the independent v4.3.0 end-user
audit while preserving the v4.3 architecture and user data compatibility.

## Fixes

- Desktop startup now resolves the FastAPI sidecar from the installed `LTCAI`
  command, an importable `ltcai_cli` module, or bundled resources, records
  sidecar errors, and exposes status/shutdown through Tauri.
- The npm package now includes the runtime requirements file used by the clean
  install bootstrap and fails honestly if dependency installation cannot run.
- Model Load no longer installs runtimes or downloads model files by default;
  missing local models and runtimes return explicit unavailable states.
- Agent execution no longer records deterministic simulation output as a real
  success when no LLM-backed model is loaded.
- The workflow screen exposes real create, import, export, and run paths backed
  by the existing workflow API.
- The desktop shell version label now comes from runtime health instead of a
  stale hard-coded RC string.
- CLI host/port flags now flow into runtime configuration, `/mode`, health, and
  SSO defaults.
- Postgres status and packaging are explicit; the npm runtime installs psycopg,
  while Postgres mode remains opt-in.
- sqlite-vec capability reports now distinguish sqlite-vec ANN from the real
  brute-force cosine fallback.
- `.latticebrain` documentation now says workspace export bundles are included
  only when present, matching archive manifests.

## Expected Artifacts

- `dist/ltcai-4.3.1-py3-none-any.whl`
- `dist/ltcai-4.3.1.tar.gz`
- `ltcai-4.3.1.tgz`
- `dist/ltcai-4.3.1.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.3.1_aarch64.dmg`

## Validation Summary

- Python compile check, ruff, unit tests, live integration tests, frontend lint,
  TypeScript/VS Code extension build, Playwright visual tests, and Tauri cargo
  check passed.
- Release artifact validation, wheel smoke test, `npm pack --dry-run`, and a
  clean npm install plus `npx ltcai doctor` passed.
- End-user replay evidence is stored under
  `output/audits/v4.3.1-fixes/`, including the final DMG screenshot
  `screenshots/10-dmg-desktop-app-rendered-final.png`.
- Runtime replay confirmed: missing model load returns 409 without opening
  outbound sockets, agent execution refuses simulation as product success,
  workflows can be created and run through real APIs, Postgres is opt-in and
  unavailable without a DSN, sqlite-vec ANN absence is labeled as a real
  brute-force cosine fallback, and `.latticebrain` manifests report
  `signed_bundles: false` when no signed bundles are present.

## Registry Policy

No external registries are published for this repair RC. PyPI, npm Registry,
VS Code Marketplace, Open VSX, and other external registries remain
unpublished.
