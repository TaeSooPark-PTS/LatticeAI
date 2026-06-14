# Lattice AI v5.2.0 - User-Focused Model Transformation

v5.2.0 makes model setup more honest and useful for real users. The model picker
is now backed by a structured capability registry, Hugging Face verification,
hardware fit notes, download/load strategy metadata, and explicit safety/license
details before any install or download action.

## Highlights

- Added `latticeai/services/model_capability_registry.py` as the structured
  source for model capability metadata.
- Added `scripts/verify_hf_model_registry.py` and `verification_report.json` for
  lightweight Hugging Face presence, config, tokenizer, and weights-hint checks.
- Kept modern multimodal candidates visible in the registry while limiting the
  user-facing load-ready catalog to currently verified families.
- Hardened `verification.verified` and verified API lists so the HF badge means
  HF presence plus config and tokenizer hints.
- Exposed weights availability as `has_weights_hint` instead of conflating it
  with load-ready verification.
- Added workspace-scoped marketplace template install registry state so personal
  and organization workspace installs do not overwrite each other.
- Updated the Library UI with multimodal badges, HF verification, hardware
  notes, load strategy, license/safety details, and consent-first setup copy.
- Captured fresh v5.2.0 release screenshots, GIF, and WebM evidence under
  `output/release/v5.2.0/`.

## Verification Summary

- HF registry verifier: all 16 curated repos present; 15 expose config/tokenizer
  hints; Pixtral remains available but not local-load verified.
- Model registry/catalog/recommendation/API tests cover the load-ready vs
  registry-only boundary.
- Workspace marketplace tests cover workspace-scoped template install registry
  behavior.
- Release validation expects exact v5.2.0 artifact filenames and warns against
  `dist/*` uploads.

## Expected Artifacts

- `dist/ltcai-5.2.0-py3-none-any.whl`
- `dist/ltcai-5.2.0.tar.gz`
- `dist/ltcai-5.2.0.vsix`
- `ltcai-5.2.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_5.2.0_aarch64.dmg`

## Release Evidence

- [Screenshot index](output/release/v5.2.0/SCREENSHOT_INDEX.md)
- [Living Brain walkthrough GIF](output/release/v5.2.0/gifs/v5.2.0-living-brain-walkthrough.gif)
- [Living Brain walkthrough WebM](output/release/v5.2.0/videos/v5.2.0-living-brain-walkthrough.webm)
