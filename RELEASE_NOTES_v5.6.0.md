# Lattice AI v5.6.0 — Brain Automation Review Center

**Date:** 2026-06-15  
**Branch:** main  
**Publish:** owner-run only; no package registry publish or deployment performed.

Lattice AI v5.6.0 adds a review-first layer for Brain automation. Automation
output can now land in a workspace-scoped Review inbox with source and
provenance metadata before the user approves, dismisses, snoozes, or reruns it.

## Highlights

- **Review Queue API**: `/automation/reviews` exposes source-aware review items
  for `workflow_run`, `trigger`, and `kg_change_digest` output.
- **Guarded actions**: approve, dismiss, snooze, and run_now are explicit state
  actions. Invalid transitions return 409.
- **Run now is not approval**: rerunning updates `payload.last_run_id` and
  `provenance.run_id` only; acceptance remains a separate approve action.
- **Opt-in automation enqueue**: TriggerService and RunExecutor enqueue review
  items only when workflows explicitly set `review_queue: true`.
- **Act Review inbox**: Act > Runs now includes a Review tab with source filters,
  pending review cards, provenance details, empty/loading/error states, and
  guarded actions.
- **OpenAPI sync**: `frontend/openapi.json` and `frontend/src/api/openapi.ts`
  include the review contract.

## Expected Artifacts (exact 5.6.0 names only)

- `dist/ltcai-5.6.0-py3-none-any.whl`
- `dist/ltcai-5.6.0.tar.gz`
- `dist/ltcai-5.6.0.vsix`
- `ltcai-5.6.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_5.6.0_aarch64.dmg`

## Preserved

- Local-first defaults and explicit consent gates.
- Legacy scheduler and workflow behavior unless `review_queue: true` is set.
- Snooze expiry is read-time only; no scheduler mutation is introduced.
- Package registry publishing remains owner-run only.

Full release guide: see [RELEASE.md](RELEASE.md).  
Changelog details: [docs/CHANGELOG.md](docs/CHANGELOG.md).  
Feature status: [FEATURE_STATUS.md](FEATURE_STATUS.md).
