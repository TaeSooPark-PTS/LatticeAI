# Lattice AI v4.3.3 - Dead-Code Cleanup Release

Release date: 2026-06-13

v4.3.3 promotes the post-cleanup `main` tree after the independent dead-code,
architecture, and runtime audit. The v4.3.2 artifacts are intentionally not
reused because tracked source and documentation changed after the v4.3.2 release
candidate.

## Highlights

- Dead-code cleanup removed obsolete paths identified during the independent
  audit while preserving compatibility shims, user data paths, and packaging
  boundaries.
- Architecture documentation now matches the local-first desktop runtime:
  Tauri desktop shell, localhost FastAPI sidecar, React/Vite SPA, Brain Core,
  storage, portability, and release artifact boundaries.
- Vercel readiness remains static-docs-only through `vercel.json` and
  `scripts/build_vercel_static.mjs`; Vercel must not deploy `server.py` as a
  hosted FastAPI app.
- README badges for PyPI, npm, VS Code Marketplace, Open VSX, CI, and license
  remain restored with clear owner-published registry caveats.
- No feature behavior changes are included beyond cleanup, safety, and
  documentation alignment.

## Validation

Validation passed for Python compile, Ruff, unit tests, live integration tests,
frontend lint, TypeScript typecheck, VS Code extension build, Playwright visual
tests, Tauri check/build, exact v4.3.3 release artifact validation, wheel
smoke, `npm pack --dry-run`, README/Markdown link check, Python package metadata
check, and Vercel static build.

Full detail: [docs/V4_3_3_VALIDATION_REPORT.md](docs/V4_3_3_VALIDATION_REPORT.md).

## Artifacts

- `dist/ltcai-4.3.3-py3-none-any.whl`
- `dist/ltcai-4.3.3.tar.gz`
- `dist/ltcai-4.3.3.vsix`
- `ltcai-4.3.3.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.3.3_aarch64.dmg`

SHA-256 hashes are recorded in
[docs/V4_3_3_VALIDATION_REPORT.md](docs/V4_3_3_VALIDATION_REPORT.md).

## External Publishing

External registries are owner-published only. This release preparation does not
run `twine upload`, `npm publish`, `vsce publish`, or `ovsx publish`.

Owner commands after inspecting the GitHub Release artifacts:

```bash
python3 -m twine upload dist/ltcai-4.3.3-py3-none-any.whl dist/ltcai-4.3.3.tar.gz
npm publish ltcai-4.3.3.tgz --access public
(cd vscode-extension && npx vsce publish --packagePath ../dist/ltcai-4.3.3.vsix)
(cd vscode-extension && npx ovsx publish ../dist/ltcai-4.3.3.vsix)
```

## Evidence

- Cleanup basis: `docs/V4_3_2_DEADCODE_AUDIT_REPORT.md`
- Prior product audit evidence: `output/audits/v4.3.2-rc/`
- Current architecture: `ARCHITECTURE.md`
- Current changelog: `docs/CHANGELOG.md`
