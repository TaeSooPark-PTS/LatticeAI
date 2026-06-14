# Lattice AI v4.6.1 - Living Brain Release Refresh

Lattice AI v4.6.1 is the publishable Living Brain release refresh. It preserves
the v4.6.0 Living Brain implementation and bumps the release line because the
v4.6.0 PyPI upload path hit PyPI's immutable-version rule: once a version/file
exists, it cannot be replaced.

## Why 4.6.1

- PyPI versions are immutable, so the release/publishable artifact set moves
  from `4.6.0` to `4.6.1`.
- v4.6.0 remains historical release history.
- v4.6.1 is the exact version for package metadata, release artifacts, Git tag,
  GitHub Release assets, README links, and owner publishing commands.

## Highlights

- Refreshed README around the current Living Brain product flow:
  Login -> Environment Analysis -> Recommended Models -> Install & Load ->
  Brain Chat.
- Replaced stale dashboard-era README evidence with fresh v4.6.1 screenshots and
  a Living Brain walkthrough GIF.
- Documented the five Brain depths: Living Brain, Memory Layer, Knowledge Layer,
  Relationship Layer, and Knowledge Graph.
- Reaffirmed that the graph emerges from the Brain as the deepest exploration
  layer rather than serving as the home dashboard.
- Updated architecture documentation for the current Tauri, React/Vite,
  FastAPI, independent `lattice_brain`, StorageEngine, SQLite default,
  PostgreSQL/pgvector opt-in, backup/restore, and `.latticebrain` portability
  reality.
- Synchronized release docs, changelog, feature status, security posture, VS Code
  extension README, and exact artifact filenames.

## Preserved

- No backend architecture redesign.
- No unrelated feature additions.
- Brain Core and `lattice_brain` isolation.
- FastAPI APIs and compatibility routes.
- Tauri desktop shell.
- StorageEngine, SQLite default, PostgreSQL/pgvector opt-in.
- Backup, restore, archive, and `.latticebrain` portability flows.
- Model runtime, agent runtime, workflow runtime, graph/search/chat/capture/
  automation/system capabilities.

## Expected Artifacts

- `dist/ltcai-4.6.1-py3-none-any.whl`
- `dist/ltcai-4.6.1.tar.gz`
- `dist/ltcai-4.6.1.vsix`
- `ltcai-4.6.1.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.6.1_aarch64.dmg`

## Evidence

- README screenshots and GIF:
  [output/release/v4.6.1/SCREENSHOT_INDEX.md](output/release/v4.6.1/SCREENSHOT_INDEX.md)
- Release refresh report:
  [docs/V4_6_1_RELEASE_REFRESH_REPORT.md](docs/V4_6_1_RELEASE_REFRESH_REPORT.md)

## Publishing Scope

This release prepares artifacts and GitHub Release assets only. It does not
publish to PyPI, npm Registry, VS Code Marketplace, or Open VSX.
