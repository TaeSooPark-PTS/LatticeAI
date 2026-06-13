# v4.5.0 Product Experience Recovery Report

Date: 2026-06-13

## Scope

v4.5.0 restores the product experience on `main` after v4.4.0. It does not
redesign `lattice_brain`, StorageEngine, FastAPI, Tauri, backup/restore, or
portability architecture.

## Restored Journey

Before v4.5.0, account, workspace, setup, environment analysis, model setup, and
mode selection existed but were scattered inside System/Library surfaces.

After v4.5.0, the first-run path is visible from the app shell:

1. Login
2. Workspace Selection
3. Environment Analysis
4. Model Recommendation
5. Model Installation
6. Model Validation
7. Mode Selection
8. Brain Usage

## Product Changes

- Added a first-run guide in the desktop app shell with direct actions for
  account, workspace, model setup, mode choice, and Brain entry.
- Persisted the selected workspace across reloads so the chosen workspace is
  used by API requests after restart.
- Reworked Library Models around the existing streamed prepare/load path.
- Kept model downloads and runtime installs behind explicit consent.
- Added Gemma 4 MLX runtime metadata checks so 12B `gemma4_unified` shows
  runtime update guidance while 26B A4B remains ready on the working path.
- Reduced Basic-mode developer leakage while keeping Advanced/Admin detail.
- Updated graph/search copy for readability, focus, and source clarity.

## Evidence

- Screenshots: `output/audits/v4.5.0-rc/screenshots/`
- GIFs: `output/audits/v4.5.0-rc/gifs/`
- Validation: `docs/V4_5_0_VALIDATION_REPORT.md`

## No-Redesign Confirmation

No storage migrations, archive format changes, FastAPI architecture changes,
Tauri shell redesign, or Brain Core extraction changes were introduced.
