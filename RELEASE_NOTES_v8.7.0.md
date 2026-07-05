# Lattice AI v8.7.0 - Runtime State Hygiene & Release Evidence Refresh

8.7.0 turns the current main-branch hardening work into a release line. The
model runtime now keeps implementation reads on the typed `ModelRuntimeState`
object, while legacy globals remain available only as a compatibility surface.
The release also refreshes the checked-in visual evidence for the current app.

## Highlights

- Model-runtime internals now prefer `ModelRuntimeState` over bare module
  globals.
- `sync_to_module_globals()` remains functional for older shims, but emits a
  `DeprecationWarning`.
- Unit coverage verifies the typed state source of truth and deprecation path.
- 8.7.0 release screenshots, walkthrough GIF/WebM, and capture notes were
  regenerated under `output/release/v8.7.0/`.
- Package/runtime/static/Tauri metadata and current-release documentation are
  synchronized to 8.7.0.

## Expected Artifacts

Use exact 8.7.0 artifact filenames only:

- `dist/ltcai-8.7.0-py3-none-any.whl`
- `dist/ltcai-8.7.0.tar.gz`
- `dist/ltcai-8.7.0.vsix`
- `ltcai-8.7.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_8.7.0_aarch64.dmg`

Package registry publishing remains owner-run.
