[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./RELEASE_NOTES_v14.1.2.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](../ko/releases/RELEASE_NOTES_v14.1.2.md)

# Lattice AI v14.1.2 — Living Synthesis

Release date: **2026-11-25**

`v14.1.2` enforces a repository-wide **zero-comments** standard across every tracked source language, removes all suppression directives by fixing the underlying code, and locks the rule into both local lint and GitHub Actions CI.

## Highlights

- **Full Comment & Suppression Removal Across All Source Languages:**
  - Stripped all human-written inline comments, block comments, docstrings, JSDoc, and suppression directives (`# noqa`, `# type: ignore`, `// @ts-expect-error`, `// eslint-disable`, `/* @vite-ignore */`, etc.) across Python, TypeScript/JavaScript, Rust, Shell, CSS, and HTML source files, preserving only machine-parsed directives (`#!/usr/bin/env ...` shebangs and `/// <reference ... />`).
  - Replaced all removed suppression directives with direct source fixes: explicit type annotations in `lattice_brain/multimodal/images.py`, `latticeai/models/router/loading.py`, and `latticeai/runtime/build_phases/foundation.py`; explicit imports in `lattice_brain/graph/_kg_fsutil.py`; `latticeai/tools/_shared.py` extraction to eliminate `E402`; concrete exception tuples and `TTFError` handling in `worker_compute_render.py`, `worker_seams.py`, `audio.py`, `ports.py`, `hnsw.py`, and `sidecar.py`; parameterized `json_each(?)` SQL in `sidecar.py`; `os.sysconf` memory probing in `worker_seams.py`; and Rust Clippy cleanups in `lattice-retrieval` and `lattice-platform`.
- **Permanent No-Comments Gate (`scripts/check_no_comments.mjs`, `package.json`, `.github/workflows/ci.yml`):**
  - Added `npm run check:no-comments`, using `@babel/parser` for TypeScript/JavaScript, Python `ast` + `tokenize`, and dedicated lexers for Rust, Shell, CSS, and HTML.
  - Wired `npm run check:no-comments` into `npm run lint` and the CI `frontend` job, and documented the permanent rule in `AGENTS.md`, `CONTRIBUTING.md`, and their Korean mirrors.

## Exact Distribution Artifacts

- Python wheel: `dist/ltcai-14.1.2-py3-none-any.whl`
- Python sdist: `dist/ltcai-14.1.2.tar.gz`
- npm tarball: `ltcai-14.1.2.tgz`
- VS Code / Cursor / Antigravity extension: `dist/ltcai-14.1.2.vsix`
- macOS Apple Silicon DMG: `src-tauri/target/release/bundle/dmg/Lattice AI_14.1.2_aarch64.dmg`
