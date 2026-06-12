# Lattice AI v4.3.2 — Independent Dead-Code, Architecture, and Runtime Audit

Date: 2026-06-13. Performed by an independent reviewer (Claude) against the
v4.3.2 release-preparation tree. Documents were treated as claims to verify,
not as truth. This pass deletes only items classified DELETE NOW; it does not
tag, create a GitHub Release, or publish to any registry.

## Method

- Built a reference graph over all 561 tracked files: Python import scan
  (static + relative), npm/pyproject/MANIFEST packaging surfaces, CI workflows,
  frontend imports, service-worker precache lists, and Markdown references.
- Module-level orphan detection plus `vulture` (min-confidence 90) over
  `latticeai`, `lattice_brain`, `tools`.
- Full baseline validation BEFORE deletions (602/602 unit tests, ruff clean,
  runtime smoke), repeated AFTER deletions.
- Runtime audit against a live FastAPI backend built from the cleaned tree.

## 1. Deleted files (DELETE NOW — all verified zero live references)

| File | Why it is dead |
| --- | --- |
| `scripts/release-0.3.1.sh` | One-shot v0.3.1 commit/tag/push script; version-pinned, dangerous if re-run; referenced only by a historical audit JSON. |
| `scripts/take_screenshots.js` | Captures retired legacy pages (`/`, `/admin`, `/graph`) deleted in the v4.1 SPA rebuild; those routes now 308-redirect. |
| `scripts/capture/` (9 files incl. README) | Playwright capture tooling for retired legacy routes/selectors (e.g. waits on `#graph`, which no longer exists) and version-pinned v3.4.0/v2.2.x media scripts. Not used by CI, tests, or release flows. |
| `scripts/generate_diagrams.py` | v3-era PNG diagram generator for `docs/images/`; outputs describe retired v3 UI; unreferenced outside historical audit JSON. |
| `clear_logs.sh` | Resets v0.x-era root log files (`server.log`, `ai_server.log`, `chat_history.json`); nothing references it; current product logs elsewhere. |
| `static/css/tokens.3ba22e37.css` | Byte-identical duplicate of `static/css/tokens.css`; zero references (verified `git grep`). |
| `latticeai/api/deps.py` | Three type aliases, never imported anywhere since v1.4.0; confirmed by import scan. |

Also removed: the five orphaned `capture:*` script entries in `package.json`.
The deletions do not touch migrations, backup/restore code, compatibility
shims, user data paths, v4.3.2 release artifacts, or README-linked docs.

## 2. Kept for compatibility

- Root deprecation shims (packaged in wheel/npm, imported by app and tests):
  `server.py`, `kg_schema.py`, `knowledge_graph.py`, `knowledge_graph_api.py`,
  `llm_router.py`, `mcp_registry.py`.
- Live root modules (imported by `latticeai`): `auto_setup.py`,
  `setup_wizard.py`, `local_knowledge_api.py`, `p_reinforce.py`,
  `telegram_bot.py`, `ltcai_cli.py`.
- `lattice_brain/*` one-line re-export modules (public Brain Core import
  surface) and `latticeai/brain/*` (the physical implementation).
- `desktop/electron/main.cjs` (documented fallback shell; `desktop:electron`).
- `latticeai/api/ui_redirects.py` and frontend `routeAliases` (legacy
  bookmark/hash-route compatibility — verified live by Playwright test
  "legacy page URLs redirect into the replacement app").
- `scripts/migrate_brain_storage.py` (migration tooling; protected class).
- `static/vendor/fonts/*` and `static/vendor/icons/*` (precached by
  `static/sw.js` offline shell; asserted by `test_t9_privacy_vendoring.py`).
- Historical `RELEASE_NOTES_v*.md` and `docs/` reports (release-history
  record; some linked from RELEASE_NOTES.md/CHANGELOG).

## 3. Needs migration first

- `static/vendor/chart.umd.min.js` and `static/vendor/marked.min.js`: no
  shipped page references them since the v4.1 legacy-page retirement, but
  they are pinned by `tests/unit/test_t9_privacy_vendoring.py`, pyproject
  `data-files`/`package-data`, and `package.json` files. Safe removal requires
  coordinated edits to that test and the packaging manifests in one change.
- `static/sw.js` vendor-font precache entries: the React SPA does not
  reference `/static/vendor/fonts` or tabler icons; if vendor assets are ever
  pruned, the SHELL list and the t9 test must be updated together.
- `get_conversation` parameter of
  `latticeai/api/security_dashboard.py::create_security_router` is accepted
  but never used (vulture, 100%); removing it changes a factory signature.

## 4. Owner decision

- `start_ai.sh` — legacy 24/7 uvicorn launcher (Korean-language, caffeinate
  loop). Still functional against `server:app` but unreferenced and predates
  the desktop product.
- Root historical docs not linked from README: `PLATFORM_COMPLETION_REPORT_v3.4.0.md`,
  `VISUAL_REBUILD_NOTES_v3.3.1.md`, `FIGMA_SPEC.md`, `STYLE_SYSTEM.md`,
  `CLOUD_BOTS.md`, `AI_PHILOSOPHY.md`, `KNOWLEDGE_GRAPH.md`, `PUBLIC_MODE.md`,
  `MODEL_POLICY.md`, plus unlinked old release notes (v3.3.0–v3.5.0, v4.0.0,
  v4.1.0, v4.3.0). Historical record vs. clutter is an owner call.
- `docs/images/` and `docs/assets/v3.4.0/` (tracked v3-era media used only by
  historical docs; excluded from npm packaging).
- Untracked local disk clutter (not part of the repo; not touched):
  `ltcai-0.2.2.tgz` … `ltcai-4.3.1.tgz`, extracted `ltcai-0.3.1/`, historical
  `dist/` artifacts, `server.log`, `ai_server.log`, `chat_history.json`,
  `telegram_chats.json`, `venv/`, `.venv/`, `.build-venv/`, `agent_workspace/`,
  local node_modules Electron payload. v4.3.2 artifacts must be preserved.
- pptx history rewrite (pre-existing FEATURE_STATUS item; force-push only).

## 5. Uncertain

- None remaining at module/file granularity. Function-level dead branches
  inside the large legacy single-file modules (`auto_setup.py`,
  `setup_wizard.py`, `telegram_bot.py`) were not exhaustively proven dead and
  were left untouched; ruff F-rules and vulture@90 report no findings there.

## 6. Architecture mismatch report (code vs ARCHITECTURE.md)

Verified as claimed: Tauri 2 primary shell with Electron fallback; React/Vite
SPA shipped from `static/app` (asset manifest consistent with tracked
assets); frontend speaks only generated-OpenAPI HTTP (318 paths, regenerated
in lint); SQLite default `StorageEngine` with honest sqlite-vec fallback
reporting; opt-in Postgres/Docker; encrypted `.latticebrain` format v2 with
fail-closed wrong-passphrase (HTTP 400 observed); backup/restore dry-run and
confirm gates; local-only startup posture per `/admin/product-hardening`;
Vercel config is a docs-only static build.

Mismatches found and addressed:

1. "Independent Python package `lattice_brain`" was overstated: only
   `core`, `archive`, and `storage/*` are standalone; graph/memory/context/
   conversation modules physically live in `latticeai/brain/` and are
   re-exported through `lattice_brain.*`. ARCHITECTURE.md now carries a
   packaging note stating the boundary is an import-path contract. (Doc fix
   only; no redesign.)
2. Root `CHANGELOG.md` stub presented a stale `[4.0.1]` top entry while
   `docs/CHANGELOG.md` is at 4.3.2; the stub now states the snapshot is
   historical and points to the current changelog.
3. Minor: the audit package's "Changed Files Since v4.3.1" manifest lists
   `static/app/assets/index-BhPuj8rT.js{,.map}` and `index-yZswHE3d.css`,
   which were superseded by the final tracked bundle
   (`index-pdzil9ac.js` / `index-CHHal8Zl.css`). Historical claim; left as-is.

## 7. Runtime audit results (post-cleanup tree, live backend)

Backend built from the cleaned tree (uvicorn `server:app`, fresh data dir):

- Startup/health: `/health` ok, version 4.3.2, mode local. `/app` serves the
  SPA shell (HTTP 200).
- Brain graph: `/knowledge-graph/graph` empty-state then populated after
  ingestion; hybrid search returns real keyword/vector/graph channels.
- Ask: `/history/conversations` durable store reachable; no-model state
  honest (no fabricated answers; `current_model: null`).
- Capture: `/upload/document` ingested a note; node appeared in the graph.
- Act: workflow create persisted a real record; agent runtime reports
  `ready:false, mode:simulation` with an honest unavailable reason.
- Library: `/models` returns catalog with no runtime falsely loaded.
- System: storage status (sqlite engine, honest brute-force-cosine fallback),
  `/admin/product-hardening` local-only posture, backup health.
- Backup/restore: backup created and verified; restore dry-run and confirmed
  restore both succeeded with manifest verification.
- `.latticebrain`: archive create (encrypted, format v2) → verify → import
  dry-run → confirmed import all succeeded; wrong passphrase fails closed (400).
- Desktop startup (Tauri DMG) and sidecar quit: not reproducible in this
  Linux audit sandbox (no macOS/Rust toolchain). `src-tauri/` is untouched by
  this cleanup, the v4.3.2 DMG passes artifact validation, and legacy-route
  redirects plus the six product pages were exercised via Playwright instead.

## 8. Validation results (after deletions)

| Check | Result |
| --- | --- |
| Python compile (`scripts/check_python.py`) | OK — 236 modules |
| Ruff (`ruff check .`) | All checks passed |
| Unit tests (`pytest tests/unit`) | 602 passed, 0 failed |
| Live integration (`pytest tests/integration` vs live server) | 9 passed, 1 skipped (live Postgres opt-in) |
| Frontend lint (`scripts/lint_frontend.mjs`) | Pass (OpenAPI export: 318 paths) |
| TypeScript typecheck (`tsc --noEmit`) | Clean |
| vscode-extension build (`tsc -p .`) | Clean |
| Playwright visual (`tests/visual/v3.spec.js`) | 12 passed |
| Tauri check/build | Not run in sandbox (no Rust/macOS toolchain); Rust sources unchanged by cleanup |
| Release artifact validation (`validate_release_artifacts.py 4.3.2 --require-vsix --require-tgz --require-dmg`) | OK (expected WARN: historical versions present in `dist/`) |
| Wheel smoke | Adapted: wheel unpacked, all shipped modules byte-compiled, server booted from the installed-wheel tree, `/health` ok 4.3.2. Full `scripts/wheel_smoke.py` venv flow needs Python ≥3.11 (sandbox has 3.10). |
| `npm pack --dry-run` | OK (291 files, 9.6 MB unpacked) |
| Docs link check (`check_markdown_links.mjs`) | Pass (README + 15 linked files) |
| Vercel static build | Pass |

Environment caveat: Python checks ran on CPython 3.10 in the audit sandbox
(project targets ≥3.11; CI runs 3.11/3.12). All 602 tests pass on 3.10 with a
`tomllib` shim; no 3.11-only syntax exists in the tree.

## 9. Scope guarantees

No migrations, backup/restore/import code, compatibility shims, user data
paths, or v4.3.2 release artifacts were deleted. No tag, GitHub Release, or
registry publish was performed.
