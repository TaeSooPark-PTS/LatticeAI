# Lattice AI v8.6.0 - Desktop Capture & Navigation Reliability

Release date: 2026-07-05

8.6.0 fixes the desktop source-capture loop. Folder selection now works from
the Tauri app after it navigates to the local FastAPI `/app` URL, and Capture
shows clear fallback feedback when the native picker cannot open.

## Highlights

- Native folder selection is available from the desktop localhost app shell.
- Capture immediately scans/connects the selected folder path.
- Web page capture keeps paste, Enter-to-save, and bare-domain `https://`
  normalization.
- Brain shell sidebar/admin navigation is covered by updated Visual Smoke tests.
- Package, static app, Tauri, readiness gates, and release documentation are
  synchronized to 8.6.0.

## Fixed

- Restored Tauri IPC access for localhost-hosted app content by allowing
  `http://127.0.0.1:*` and `http://localhost:*` in the desktop capability.
- Added visible folder-picker fallback messaging instead of silent no-op
  behavior.
- Removed negative letter spacing from updated frontend shell styling.

## Expected Artifacts

- `dist/ltcai-8.6.0-py3-none-any.whl`
- `dist/ltcai-8.6.0.tar.gz`
- `dist/ltcai-8.6.0.vsix`
- `ltcai-8.6.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_8.6.0_aarch64.dmg`
