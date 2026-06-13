# Lattice AI v4.5.0 RC - Product Experience Recovery

Release date: 2026-06-13

v4.5.0 restores the original Lattice AI product journey on top of the v4.4.0
physical Brain extraction. It preserves `lattice_brain`, StorageEngine, FastAPI,
Tauri, backup/restore, and portability architecture.

## Highlights

- **First-run journey restored.** The app shell now guides Login -> Workspace
  Selection -> Environment Analysis -> Model Recommendation -> Model
  Installation -> Model Validation -> Mode Selection -> Brain Usage.
- **Model setup is explicit.** Library Models shows Environment Analysis,
  Recommended Models, Install, Download Progress, Validate, Load, and Ready.
  Runtime installs and model downloads require visible consent.
- **Gemma 4 regression guarded.** Gemma 4 MLX models are marked unsupported when
  the installed MLX-VLM runtime lacks the Gemma 4 `gemma4_unified` component.
  Raw loader errors are replaced by friendly recovery guidance and alternatives.
- **Basic mode is product-first.** Basic hides endpoint/module leakage in shared
  status badges, graph copy, model cards, and system readiness. Advanced/Admin
  keep diagnostic detail.
- **Graph readability improved.** Brain graph/search copy focuses on ideas,
  relationships, sources, focus, filtering, and readability.

## Compatibility

- No migration is required for user data, backups, archives, workspaces, or
  graph storage.
- v4.4.0 `lattice_brain` extraction boundaries remain intact.
- Model downloads and cloud calls remain opt-in.

## Artifacts

- `dist/ltcai-4.5.0-py3-none-any.whl`
- `dist/ltcai-4.5.0.tar.gz`
- `ltcai-4.5.0.tgz`
- `dist/ltcai-4.5.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.5.0_aarch64.dmg`

## Validation

See [docs/V4_5_0_VALIDATION_REPORT.md](docs/V4_5_0_VALIDATION_REPORT.md) for
the full RC validation matrix and artifact hashes.

## External Publishing

This RC does not create a tag or GitHub Release and does not publish to PyPI,
npm, VS Code Marketplace, or Open VSX.
