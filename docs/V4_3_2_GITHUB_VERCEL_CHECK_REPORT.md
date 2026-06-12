# Lattice AI v4.3.2 GitHub / Vercel Check Report

Date: 2026-06-13

## Decision

Lattice AI v4.3.2 is a local-first desktop product. Vercel must not deploy the
runtime, must not auto-detect `server.py` as a FastAPI entrypoint, and must not
host a fake cloud product.

Vercel is configured as a static documentation-only Git check.

## Failure Addressed

Observed Vercel failure:

```text
Found server.py but it does not define a top-level app FastAPI instance...
```

That detection path is wrong for this repository because the production runtime
is the Tauri desktop app plus a localhost FastAPI sidecar.

## Configuration

`vercel.json` now explicitly sets:

- `"framework": null` so the project uses Vercel's generic "Other" preset.
- `"installCommand": "true"` as an explicit no-op install step; the static
  status page uses only Node built-ins.
- `"buildCommand": "node scripts/build_vercel_static.mjs"`.
- `"outputDirectory": "vercel-static"`.
- A rewrite from all routes to `/index.html`.

The static build script writes `vercel-static/index.html`, a documentation page
that explains the desktop/localhost runtime boundary. It does not import
`server.py`, call FastAPI, run tests as an app, or expose placeholder product UI.

This follows Vercel's documented project configuration model: `framework` can be
set to `null` for the "Other" preset, and `buildCommand` / `outputDirectory`
override project build settings.

References:

- https://vercel.com/docs/project-configuration/vercel-json
- https://vercel.com/docs/builds/configure-a-build

## GitHub Workflow Notes

- `.github/workflows/ci.yml` runs Python compile, ruff, unit tests, integration
  smoke, Python build, twine check, and wheel smoke.
- `.github/workflows/visual.yml` runs Playwright visual smoke.
- `.github/workflows/release.yml` builds and validates artifacts on tag push
  only. It does not publish packages to external registries.

## Validation

Release-prep validation was rerun after the Vercel fix:

- `npm run vercel:build`
- Vercel config JSON parse check
- README badge link validation
- Markdown link check for README-linked docs
- Mermaid block sanity check for `ARCHITECTURE.md`
- `npx --yes vercel@54.12.2 build` reached local CLI project-linkage
  validation and stopped with `project_settings_required` because this checkout
  does not contain `.vercel` project settings or credentials. The committed
  static build command itself passed.

See `docs/V4_3_2_VALIDATION_REPORT.md` for the final command results.

## Result

PASS. Vercel is now an explicit static documentation-only check and should not
try to auto-detect or deploy the local FastAPI sidecar.
