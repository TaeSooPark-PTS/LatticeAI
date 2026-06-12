# Lattice AI v4.3.2 GitHub / Vercel Check Report

Date: 2026-06-13

## GitHub Status

Repository: `TaeSooPark-PTS/LatticeAI`

Observed with `gh run list --branch main --limit 8`:

- `CI` succeeded for v4.3.2 RC commit
  `8f3d182ee81bb395722ebab792dfd70f35e19e96`.
- `Visual Smoke` succeeded for v4.3.2 RC commit
  `8f3d182ee81bb395722ebab792dfd70f35e19e96`.
- Recent v4.3.1, v4.3.0, and v4.2.0 main checks were also green.

## GitHub Workflow Notes

- `.github/workflows/ci.yml` runs Python compile, ruff, unit tests, integration
  smoke, Python build, twine check, and wheel smoke.
- `.github/workflows/visual.yml` runs Playwright visual smoke.
- `.github/workflows/release.yml` builds and validates artifacts on tag push
  only. It does not publish packages to external registries.

## Vercel Status

No committed `vercel.json` or `.vercel/project.json` existed before this
release-prep pass. Since Lattice AI is a local desktop product, Vercel should
not attempt to build or host the runtime.

## Vercel Fix

Added:

- `vercel.json`
- `scripts/build_vercel_static.mjs`
- `npm run vercel:build`

The Vercel build now generates `vercel-static/index.html`, a documentation-only
page that states the runtime is the desktop app plus localhost FastAPI sidecar.
It does not host a fake product UI, call cloud services, or deploy the desktop
runtime.

## Validation

- `npm run vercel:build` passed and generated `vercel-static/index.html`.
- `vercel-static/` is ignored by git.

## Result

PASS. GitHub checks were green for the v4.3.2 RC commit, and Vercel has an
explicit harmless configuration path for future Git integration checks.
