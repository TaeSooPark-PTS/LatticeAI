# v4.6.1 Living Brain Release Refresh Report

## Scope

v4.6.1 is the publishable Living Brain release refresh after the v4.6.0 PyPI
immutability block. The work intentionally does not redesign backend
architecture or add unrelated product features.

## Completed Refresh Areas

- Synchronized then-current release version metadata to `4.6.1`.
- Reframed README around the current Living Brain flow:
  Login -> Environment Analysis -> Recommended Models -> Install & Load ->
  Brain Chat.
- Documented the five Brain depths: Living Brain, Memory Layer, Knowledge Layer,
  Relationship Layer, and Knowledge Graph.
- Kept the Knowledge Graph positioned as the deepest Brain exploration layer,
  not a standalone dashboard or home screen.
- Updated ARCHITECTURE.md for the current Tauri, React/Vite, FastAPI,
  independent `lattice_brain`, StorageEngine, SQLite default, optional
  PostgreSQL/pgvector, backup/restore, and `.latticebrain` portability reality.
- Added v4.6.1 release notes and synchronized the changelog, feature status,
  security policy, VS Code extension README, and release guide.

## Evidence

Fresh v4.6.1 screenshots and walkthrough media are indexed in
[output/release/v4.6.1/SCREENSHOT_INDEX.md](../output/release/v4.6.1/SCREENSHOT_INDEX.md).

## Expected Artifacts

- `dist/ltcai-4.6.1-py3-none-any.whl`
- `dist/ltcai-4.6.1.tar.gz`
- `dist/ltcai-4.6.1.vsix`
- `ltcai-4.6.1.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.6.1_aarch64.dmg`

## Validation Checklist

The final release report records command results, artifact SHA256 hashes, commit,
tag, push, and GitHub Release URL. External package registries are not published
by this refresh.
