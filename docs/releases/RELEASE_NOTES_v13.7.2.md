[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./RELEASE_NOTES_v13.7.2.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](../ko/releases/RELEASE_NOTES_v13.7.2.md)

# Lattice AI v13.7.2 — Living Synthesis (2026-11-24)

> **Status: canonical** — current release note (Canonical Domain Module Paths & Flat Crate-Root Re-export Removal).

Migrates every caller across the Rust workspace (`lattice-host`, `lattice-chat`, `lattice-platform`, `lattice-agent`, `lattice-retrieval`, `src-tauri`, and all integration tests) from flat crate-root re-exports to canonical domain module paths (`lattice_platform::<domain>::*`, `lattice_agent::<group>::*`), removes the obsolete flat compatibility re-export maps from `lattice-agent` and `lattice-platform`, and removes the last unused `lattice_chat::crate_ready()` scaffold placeholder.

## What Changed

- **Canonical Domain Module Paths (`rust/lattice-agent`, `rust/lattice-platform`, `rust/lattice-host`, `rust/lattice-chat`, `rust/lattice-retrieval`):**
  - Migrated 77+ Rust source and integration-test files (including `rust/lattice-host/src/gateway/product.rs`) to import directly from canonical domain paths (`lattice_platform::{adminops,governance,knowledge,modelops,shell,toolsurface,workspaceos}::*` and `lattice_agent::{content,kernel,parse,prompts,seam,surface,tools}::*`).
  - Removed the obsolete flat crate-root `pub use` compatibility maps from `rust/lattice-agent/src/lib.rs` and `rust/lattice-platform/src/lib.rs`, leaving a strictly domain-scoped public API surface.
  - Removed the last unused `lattice_chat::crate_ready()` scaffold stub from `rust/lattice-chat/src/lib.rs`.
- **Documentation & Crate Maps (`AGENTS.md`, `rust/lattice-agent/ARCHITECTURE.md`, `rust/lattice-platform/ARCHITECTURE.md`, `docs/ko/crates/`):**
  - Updated crate architecture docs and `AGENTS.md` (EN & KO) to reflect that flat compatibility maps were retired in `13.7.2` and all callers use canonical domain paths.
  - Synchronized all canonical EN/KO docs, release notes, evidence (`output/release/v13.7.2`), and exact artifact manifests to `13.7.2`.

## Exact Build Artifacts

- `dist/ltcai-13.7.2-py3-none-any.whl`
- `dist/ltcai-13.7.2.tar.gz`
- `ltcai-13.7.2.tgz`
- `dist/ltcai-13.7.2.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_13.7.2_aarch64.dmg`
