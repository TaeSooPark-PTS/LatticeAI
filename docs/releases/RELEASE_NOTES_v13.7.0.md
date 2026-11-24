[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./RELEASE_NOTES_v13.7.0.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](../ko/releases/RELEASE_NOTES_v13.7.0.md)

# Lattice AI v13.7.0 — Living Synthesis (2026-11-24)

> **Status: historical** — archived release note for v13.7.0 (Native Rust Obsidian & Interop Bridges, Pure ESM Vite/Vitest Configs, `v13.0.0+` Release Ledger & Modernized Architecture).

Closes the remaining Track 5 interop gap by replacing legacy forwarding to retired Python routes with native Rust `GraphWriter` ingestion for Obsidian vaults (`[[wikilink]]` `LINKS_TO` edges), Notion directory/`.zip` exports, Git commit history, and local `.eml`/`.ics` mail/calendar files; migrates Vite 8 and Vitest 5 configs to native ESM `.mts` (`vite.config.mts`, `vitest.config.mts`) eliminating all `configLoader: 'native'` warnings; prunes pre-`v13.0.0` release notes (`v13.0.0+` floor); and modernizes `ARCHITECTURE.md` and `docs/ko/ARCHITECTURE.md` to the current `13.7.0` architecture.

## What Changed

- **Native Rust Obsidian & Interop Bridges (`rust/lattice-ingest/src/local_files_api/interop.rs`, `rust/lattice-ingest/src/local_files_api/ingest.rs`, `rust/lattice-ingest/tests/local_files_replay.rs`):**
  - Implemented `ingest_obsidian_native()` directly over `GraphWriter` and `NoteIngestor`, extracting `#tags` and `[[wikilink]]`/`[[target|alias]]`/`[[target#heading]]` references and creating `LINKS_TO` edges via `GraphWriter::upsert_edges`, with full `dry_run` preview support.
  - Implemented `ingest_interop_native()` for `notion` (directory & `.zip` export with Notion 32-hex UUID suffix stripping), `git` (local repository commit history & changed-file stats), and `mail` (local `.eml` and `.ics` `VEVENT` parsing), eliminating legacy forwarding to retired Python worker routes.
- **Native ESM Vite 8 & Vitest 5 Configs (`vite.config.mts`, `vitest.config.mts`, `tsconfig.json`):**
  - Migrated `vite.config.ts` and `vitest.config.ts` to `.mts` using `fileURLToPath` + `readFileSync`, removing CommonJS `__dirname` and silencing Vite 8's `configLoader: 'native'` deprecation warning cleanly ahead of Vite 9.
- **`v13.0.0+` Release Ledger & Modernized `ARCHITECTURE.md` (`ARCHITECTURE.md`, `docs/ko/ARCHITECTURE.md`, `RELEASE.md`, `RELEASE_NOTES.md`, `docs/CHANGELOG.md`, `SECURITY.md`, `scripts/check_current_release_docs.mjs`):**
  - Pruned all pre-`v13.0.0` (`v12.x`) release note files and ledger entries across EN and KO docs and updated `scripts/check_current_release_docs.mjs` and `tests/unit/test_server_app_decomposition.py` to enforce the `v13.0.0+` floor.
  - Rewrote `ARCHITECTURE.md` and `docs/ko/ARCHITECTURE.md` concisely around the current `13.7.0` 9-crate Rust host, 20-route Python worker, native interop bridges, TS 7 / `.mts` toolchain, and local-first governance boundaries, removing stale pre-v13 migration history.

## Exact Build Artifacts

- `dist/ltcai-13.7.0-py3-none-any.whl`
- `dist/ltcai-13.7.0.tar.gz`
- `ltcai-13.7.0.tgz`
- `dist/ltcai-13.7.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_13.7.0_aarch64.dmg`
