# Lattice AI v4.5.1 RC - Product Reimagining

Release date: 2026-06-13

v4.5.1 reimagines the Lattice AI desktop product surface on top of the v4.5.0
capability recovery. It preserves `lattice_brain`, StorageEngine, FastAPI,
Tauri, backup/restore, model runtimes, APIs, and portability architecture.

## Highlights

- **New product shell.** The old rail/dashboard presentation is replaced by a
  compact premium chrome, command palette, responsive mobile drawer, and ambient
  brain canvas.
- **New navigation model.** Home, Ask, Add, Automate, Library, and Care become
  the visible product rooms while legacy hash routes remain compatible.
- **New onboarding journey.** First-run setup now reads as Make it yours ->
  Choose a space -> Meet your Mac -> Pick a brain -> Install locally -> Try a
  question -> Set the pace -> Explore memory.
- **New visual language.** The global style system moves to a calm
  carbon/warm-white base with jade, amber, violet, blue, and coral accents,
  fixed responsive type sizing, and 8px-or-smaller cards.
- **Capability preservation.** Graph, search, chat, capture, automation,
  model setup, workspaces, backups, portability, and system safety continue to
  use the existing backend APIs.

## Compatibility

- No data migration is required.
- Legacy route aliases still resolve into the React SPA.
- Model downloads, cloud calls, Docker, network pairing, and publishing remain
  explicit opt-in actions.

## Artifacts

- `dist/ltcai-4.5.1-py3-none-any.whl`
- `dist/ltcai-4.5.1.tar.gz`
- `ltcai-4.5.1.tgz`
- `dist/ltcai-4.5.1.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.5.1_aarch64.dmg`

## Validation

See [docs/V4_5_1_VALIDATION_REPORT.md](docs/V4_5_1_VALIDATION_REPORT.md) for
the full RC validation matrix, screenshots, GIFs, and artifact results.

## External Publishing

This RC does not create a tag or GitHub Release and does not publish to PyPI,
npm, VS Code Marketplace, or Open VSX.
