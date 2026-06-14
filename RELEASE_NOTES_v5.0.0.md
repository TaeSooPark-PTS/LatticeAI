# Lattice AI v5.0.0 Release Notes

Lattice AI v5.0.0 is the Multilingual Brain Foundation Release.

It starts the major-version cleanup line by preserving the working
AgentRuntime, ToolRegistry, Brain Core, Admin Console, graph, storage,
portability, and release foundations while making the everyday product usable
in Korean or English from first launch through Brain exploration.

## What Changed

- Added a persisted Korean/English language preference (`lattice.language`) to
  the React app state.
- Added first-run language selection before the Brain ritual begins.
- Localized Login, Environment Analysis, Recommended Models, Install/Download,
  Brain Chat, Memory/Topic/Relationship/Graph controls, save feedback, overview
  cards, graph fallback copy, and Admin header labels.
- Updated visual tests so the Korean path is selected explicitly and remains
  covered by the existing first-run and Brain-depth checks.
- Bumped synchronized Python, npm, VSIX, Tauri, runtime constants, and static
  metadata to `5.0.0`.

## Collaboration Notes

- `pts_claudecode` reviewed the technical-debt order and recommended:
  config centralization -> KG stabilization -> ToolRegistry characterization ->
  AgentRuntime extraction -> server/app-factory decomposition.
- `pts_grok` reviewed the product direction and prioritized visible language
  choice, easy model preparation, and clear separation between user Brain and
  Admin views for older non-technical users.
- `pts_openclaw` implemented the user-facing 5.0.0 foundation and will use the
  collaboration guidance for the next deeper refactor sequence.

## Expected Artifacts

- `dist/ltcai-5.0.0-py3-none-any.whl`
- `dist/ltcai-5.0.0.tar.gz`
- `dist/ltcai-5.0.0.vsix`
- `ltcai-5.0.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_5.0.0_aarch64.dmg`
