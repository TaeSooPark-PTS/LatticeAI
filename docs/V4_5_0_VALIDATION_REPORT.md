# v4.5.0 Validation Report

Date: 2026-06-13

## Status

Local RC validation passed on macOS using Python 3.12.13
(`/tmp/ltcai-v450-py312/bin/python`).

## Required Matrix

| Check | Result |
| --- | --- |
| Python compile | PASS: `npm run check:python` |
| Ruff | PASS: `python -m ruff check .` |
| Unit tests | PASS: `npm run test:unit` (`607 passed`) |
| Integration tests | PASS: `npm run test:integration` against a live local server (`9 passed`, `1 skipped`) |
| `lattice_brain` isolation tests | PASS: `tests/unit/test_lattice_brain_isolation.py` |
| no `lattice_brain` -> `latticeai` import test | PASS: isolation import guard |
| graph/search/ingestion tests | PASS: focused graph, search, and ingestion suite (`79 passed`) |
| backup/restore tests | PASS: `tests/unit/test_kg_portability.py`, `tests/unit/test_v42_brain_storage.py` |
| `.latticebrain` archive tests | PASS: encrypted archive round trip, tamper rejection, dry-run restore |
| frontend lint/typecheck | PASS: `npm run lint`, `npm run typecheck` |
| Playwright | PASS: `npm run test:visual` (`14 passed`) |
| Tauri cargo check | PASS: `npm run desktop:tauri:check` |
| Tauri DMG build | PASS: `npm run release:artifacts` |
| release artifact validation | PASS: `npm run release:validate` |
| wheel smoke | PASS: `scripts/wheel_smoke.py --wheel dist/ltcai-4.5.0-py3-none-any.whl` |
| npm pack dry-run | PASS: `npm pack --dry-run` |
| onboarding/model/Gemma/first-run validation | PASS: Playwright coverage plus screenshots/GIF evidence |

## Notes

- The first integration run was executed without the live local server and
  failed with connection-refused setup errors. It was rerun against
  `http://127.0.0.1:8899` and passed.
- The Postgres live migration integration test remains skipped unless the
  dedicated Postgres fixture is available.
- Vite reports the existing large frontend bundle warning during production
  builds.
- Tauri/Rust reports an upstream future-incompatibility warning for
  `block v0.1.6`.
- SHA-256 artifact hashes were captured after the final local artifact rebuild
  so this report can be packaged without creating self-referential hash churn.

## Evidence

- Screenshots: `output/audits/v4.5.0-rc/screenshots/`
- Walkthrough GIF: `output/audits/v4.5.0-rc/gifs/v4.5.0-first-run-walkthrough.gif`
- Validated artifacts:
  - `dist/ltcai-4.5.0-py3-none-any.whl`
  - `dist/ltcai-4.5.0.tar.gz`
  - `ltcai-4.5.0.tgz`
  - `dist/ltcai-4.5.0.vsix`
  - `src-tauri/target/release/bundle/dmg/Lattice AI_4.5.0_aarch64.dmg`

## External Publishing

No PyPI, npm Registry, VS Code Marketplace, or Open VSX publish command is part
of this RC validation.
