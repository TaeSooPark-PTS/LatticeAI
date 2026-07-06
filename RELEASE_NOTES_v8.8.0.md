# Lattice AI v8.8.0 - Brain Core Extraction & Recall Proof Hardening

8.8.0 prepares `lattice_brain` to stand as a cleaner Brain Core package while
making answer proof and conversation behavior more useful in the product UI.
Internal-only compatibility layers are removed, remaining public root shims are
still tracked, and recall citations now explain which query terms matched.

## Highlights

- Removed flat `lattice_brain.*` shim modules, the deprecated `latticeai.brain`
  namespace, and the `latticeai.services.agent_runtime` alias.
- `legacy_shim_report()` now reports remaining shims, removed shims, and any
  lingering removed files separately.
- AgentRuntime validates roles at the boundary, synthesizes contracts for
  legacy run records, and stores the clamped retry budget.
- Brain Chat adds conversation history controls, stop/regenerate/copy actions,
  and more visible ingestion progress.
- Memory recall filters zero-evidence rows when stronger lexical matches exist
  and exposes matched terms plus confidence for citations.

## Expected Artifacts

Use exact 8.8.0 artifact filenames only:

- `dist/ltcai-8.8.0-py3-none-any.whl`
- `dist/ltcai-8.8.0.tar.gz`
- `dist/ltcai-8.8.0.vsix`
- `ltcai-8.8.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_8.8.0_aarch64.dmg`

Package registry publishing remains owner-run.
