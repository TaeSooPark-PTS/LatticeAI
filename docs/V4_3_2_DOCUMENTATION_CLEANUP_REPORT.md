# Lattice AI v4.3.2 Documentation Cleanup Report

Date: 2026-06-13

## Scope

This release-preparation pass rewrote the public README and audited every
Markdown file linked directly from it. The goal was publishing readiness, not
feature work.

## README Changes

- Restored modern package/status badges for PyPI, npm, VS Code Marketplace,
  Open VSX, CI, and license while keeping the v4.3.2 registry-publishing caveat.
- Rewritten around observed v4.3.2 self-audit behavior.
- Removed old v3 product-tour screenshots from the public tour.
- Added GitHub-renderable screenshots/GIFs from
  `output/audits/v4.3.2-rc/`.
- Added required tour sections:
  - Desktop startup
  - Brain graph
  - Ask
  - Capture
  - Act
  - Library
  - System
  - Backup / Restore
  - `.latticebrain` portability
- Added current artifact list using exact v4.3.2 filenames.
- Added known limitations and owner-controlled publishing caveats.

## Linked Markdown Files Audited

- `ARCHITECTURE.md`
- `FEATURE_STATUS.md`
- `RELEASE_NOTES.md`
- `RELEASE_NOTES_v4.3.2.md`
- `RELEASE.md`
- `SECURITY.md`
- `docs/CHANGELOG.md`
- `docs/V4_3_2_GRAPH_UX_REPORT.md`
- `docs/V4_3_2_PRODUCT_POLISH_REPORT.md`
- `docs/V4_3_2_SELF_AUDIT_REPORT.md`
- `docs/V4_3_2_VALIDATION_REPORT.md`
- `docs/V4_3_2_DOCUMENTATION_CLEANUP_REPORT.md`
- `docs/V4_3_2_GITHUB_VERCEL_CHECK_REPORT.md`
- `docs/V4_3_2_INDEPENDENT_AUDIT_PACKAGE.md`
- `docs/V4_DIGITAL_BRAIN_RECOVERY.md`

## Updates Made

- `ARCHITECTURE.md` is now a diagram-first system map covering Tauri startup,
  the sidecar lifecycle, React/Vite, FastAPI, `lattice_brain`, StorageEngine,
  SQLite default mode, PostgreSQL opt-in mode, archive portability,
  backup/restore, privacy boundaries, release artifacts, and Vercel static docs.
- `FEATURE_STATUS.md`, `RELEASE_NOTES.md`, and `docs/CHANGELOG.md` now make
  clear that older sections are historical records.
- `FEATURE_STATUS.md` now describes Vercel as a static documentation-only check
  instead of the obsolete broken FastAPI deployment state.
- `SECURITY.md` now references v4.3.2 and clarifies default local-only external
  communication behavior.
- `docs/V4_3_2_GITHUB_VERCEL_CHECK_REPORT.md` documents the `server.py`
  auto-detection failure and the explicit static-only Vercel configuration.
- `docs/V4_3_2_SELF_AUDIT_REPORT.md` now includes README-specific Ask and
  Capture evidence.
- `docs/V4_3_2_VALIDATION_REPORT.md` now includes Markdown-link and Vercel
  build validation, README badge link validation, and Mermaid sanity checks.

## Link Validation

`npm run docs:check-links` validates README local links, README image/GIF paths,
and links inside README-linked Markdown files. External web links are skipped by
this local checker.

README badge URLs were validated separately because the local Markdown checker
intentionally skips external web links.

## Result

PASS. README-linked current documentation no longer contradicts the v4.3.2
self-audit evidence, current architecture, feature status, or release notes.
