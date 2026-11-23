[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./RELEASE_NOTES_v13.4.1.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](../ko/releases/RELEASE_NOTES_v13.4.1.md)

# Lattice AI v13.4.1 — Living Synthesis (2026-11-23)

> **Status: canonical** — current release note (Dependency Compatibility & Maintenance).

Dependency compatibility & maintenance release resolving Dependabot PRs `#58`, `#60`, and `#61` across the full stack.

## What Changed

- **Frontend (`package.json`, `package-lock.json`, `tsconfig.json`, `frontend/src/vite-env.d.ts`):**
  - Added `typescript` override in `package.json` so `openapi-typescript@7.13.0` resolves cleanly with `typescript@7.0.2` under `npm ci`.
  - Updated `tsconfig.json` for TypeScript 7 (removed deprecated `baseUrl`, switched `paths` to `./frontend/src/*`) and added `frontend/src/vite-env.d.ts` (`/// <reference types="vite/client" />`) for CSS side-effect imports.
  - Updated `vitest@5.1.1` / `@vitest/coverage-v8@5.1.1` / `jsdom@31` / `@testing-library/jest-dom@8` and preserved 100% branch, line, function, and statement coverage across all 111 frontend test files.
- **Rust Workspace (`rust/` & `src-tauri/`):**
  - Migrated `sha2` 0.11 / `digest` 0.11 callers to `hex_lower` / `sha256_text` / `sha256_bytes`.
  - Migrated `rusqlite` 0.43 `usize` bindings to `i64`.
  - Updated `fancy-regex` 0.20 `Captures<'_, str>` type annotations in `lattice-core/src/redact.rs`.
  - Updated `axum` 0.8 extractors (native async `FromRequestParts` without `#[async_trait]`) and `scrypt` 0.12 3-argument `Params::new` in `lattice-auth`.
  - Updated `zip` 9 (`ZipFile<'_, R>`, `Result<Cow<'_, str>, ZipError>`, `SimpleFileOptions`) in `lattice-platform`, `reqwest` 0.13 in `lattice-ingest`, and Tauri desktop crates (`tauri` 2.11.6, `plist` 1.8.0, `time` 0.3.55) in `src-tauri`.

## Exact Build Artifacts

- `dist/ltcai-13.4.1-py3-none-any.whl`
- `dist/ltcai-13.4.1.tar.gz`
- `ltcai-13.4.1.tgz`
- `dist/ltcai-13.4.1.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_13.4.1_aarch64.dmg`
