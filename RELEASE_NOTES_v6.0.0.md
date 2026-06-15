# Lattice AI v6.0.0 - Product Reset / Review Center Completion

Lattice AI v6.0.0 starts the product-quality reset branch without claiming the
aspirational 100/100 target. The evidence-backed improvement is concentrated on
Review Center completion, OpenAPI-derived review typing, frontend feature
ownership, and v6 quality documentation.

## Highlights

- Added Pending, Snoozed, and All filters to Review Center.
- Added explicit Unsnooze backend policy and
  `POST /automation/reviews/{item_id}/unsnooze`.
- Added frontend Unsnooze action and clearer `snoozed_until` display.
- Preserved `run_now != approve`; run now remains preview/regenerate.
- Kept snooze expiry read-time only, with no mutation on read.
- Extracted Review Center UI into `frontend/src/features/review/`.
- Regenerated OpenAPI artifacts and moved ReviewItem frontend typing to
  generated component schemas.
- Added v6 plan, architecture review, UX review, and quality scorecard docs.
- Synchronized package/runtime/static metadata to `6.0.0`.

## Expected Artifacts

- `dist/ltcai-6.0.0-py3-none-any.whl`
- `dist/ltcai-6.0.0.tar.gz`
- `dist/ltcai-6.0.0.vsix`
- `ltcai-6.0.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_6.0.0_aarch64.dmg`

## Validation Notes

The branch must not be described as release-ready until the full v6 validation
gate is run and reported. Package publishing, GitHub Release creation, artifact
upload, and merge to `main` are intentionally out of scope for this branch.
