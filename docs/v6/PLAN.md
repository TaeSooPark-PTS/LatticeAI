# Lattice AI v6.0.0 Product Reset Plan

Branch: `feat/v6-product-reset-100-point-uplift`

This plan tracks the v6.0.0 product-quality uplift. The target is aggressive,
but completion claims must stay evidence-based.

## Non-negotiables

- Preserve Lattice AI as a local-first Digital Brain.
- Preserve Brain archives, backup, restore, and local-first guarantees.
- Do not publish packages, create GitHub Releases, upload artifacts, or merge to
  `main` from this work branch.
- Keep external communication explicit-consent only.
- Do not claim test, build, score, or product-quality outcomes without evidence.

## Work Lanes

### Lane A — Review Center Completion

Required:

- Pending, Snoozed, and All filters.
- Explicit `unsnooze` policy and API.
- Snoozed items show `snoozed_until`.
- Snoozed state is reversible without mutating on read.
- `run_now` remains preview/regenerate and never approves.

Current implementation status:

- Backend unsnooze policy and route added.
- Frontend Review Center extracted into `frontend/src/features/review/`.
- Pending/Snoozed/All filters and Unsnooze action added.

### Lane B — OpenAPI & Strict Typing

Required:

- Regenerate backend OpenAPI schema.
- Regenerate frontend TypeScript definitions.
- Drive Review Center client types from generated OpenAPI schemas.

Current implementation status:

- OpenAPI regenerated after adding `unsnooze`.
- `ReviewItem` and `ReviewItemList` now alias generated OpenAPI component
  schemas in `frontend/src/api/client.ts`.

### Lane C — Frontend Architecture Reset

Required:

- Reduce `Act.tsx` responsibility.
- Extract Review Center components/helpers into feature-owned modules.

Current implementation status:

- `ReviewInbox`, `ReviewCard`, and `reviewHelpers` extracted under
  `frontend/src/features/review/`.

### Lane D — UX/Product Simplification

Required:

- Make it obvious what Lattice is, where data lives, what model is running, what
  leaves the machine, and what to do next.

Current implementation status:

- Review Center copy now emphasizes reviewable automation, reversible snoozing,
  and run-now-as-preview semantics.
- Broader Brain Home/onboarding simplification remains follow-up unless more
  branch time is allocated.

### Lane E — Backend Architecture Reset

Required:

- Decompose `app_factory.py` while preserving lazy imports and compatibility.

Current implementation status:

- No large app-factory split is claimed yet. Review Queue remains in service/API
  modules with thin router wiring.

### Lane F — Brain Core Boundary Hardening

Required:

- Verify `lattice_brain` does not depend on `latticeai`.

Current implementation status:

- Boundary verification is tracked in `ARCHITECTURE_REVIEW.md`.

### Lane G — Packaging & Release Stability

Required:

- Synchronize version constants and release docs to `6.0.0`.
- Build and validate exact artifacts if the branch reaches release-candidate
  state.

Current implementation status:

- Version metadata is synchronized to `6.0.0`.
- Artifact build/validation still must run before final release-readiness claims.

## Acceptance Gates

Before final report:

- `npm run check:python`
- `node scripts/run_python.mjs -m ruff check .`
- `npm run lint`
- `npm run typecheck`
- `npm run test:unit`
- `npm run test:integration`
- `npm run docs:check-links`
- `npm run desktop:tauri:check`
- `npm run build:assets`
- `npm run build:python`
- `python scripts/wheel_smoke.py --wheel dist/ltcai-6.0.0-py3-none-any.whl`
- `npm run release:validate` if release artifacts are produced.
- `npm run test:visual` if available.
