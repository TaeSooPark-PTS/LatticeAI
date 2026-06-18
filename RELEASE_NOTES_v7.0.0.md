# Lattice AI v7.0.0 - Brain Productization Loop

Released: 2026-06-18

Lattice AI v7.0.0 is the first release line that treats the Brain proof work as
a product loop rather than a supporting capability. The target experience is
simple: add a source, ask a question, see the source-backed memory proof, then
verify that the same Brain evidence survives model changes.

## Highlights

- Brain Home now starts with source ingestion: files, local folder paths, notes,
  and web URLs are available from the first screen.
- Assistant responses can render Memory proof and source citation cards below
  the answer, so users see what the Brain recalled instead of trusting a plain
  chat bubble.
- The model-continuity demo lets users recheck the same Brain evidence and jump
  to the model page when they want to switch the current model.
- First-run value is compressed into a five-minute loop: add source, ask, see
  proof, then deepen into graph/search or change models.
- CI now includes a deterministic recall/KG quality eval for durable evidence,
  citation source availability, graph counts, vector counts, and
  model-continuity proof.

## Validation

- Frontend typecheck and i18n lint
- Vite asset build with route/rich-page code-splitting preserved
- Brain recall/KG quality eval
- Visual smoke with mocked Brain proof and ingestion endpoints
- Release artifact validation and smoke checks

## Expected Artifacts

- `dist/ltcai-7.0.0-py3-none-any.whl`
- `dist/ltcai-7.0.0.tar.gz`
- `dist/ltcai-7.0.0.vsix`
- `ltcai-7.0.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_7.0.0_aarch64.dmg`

Package registry publishing remains owner-run. GitHub tag/release creation does
not publish to PyPI, npm, VS Code Marketplace, Open VSX, Cursor, or
Antigravity.
