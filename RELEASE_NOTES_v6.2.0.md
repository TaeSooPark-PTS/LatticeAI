# Lattice AI v6.2.0 Release Notes

## Product Decomposition / Release Smoke Automation

v6.2.0 keeps the v6 Digital Brain experience stable while reducing the largest
frontend and backend composition surfaces. It prepares the product for faster
post-release iteration without changing package publishing ownership.

### Highlights

- `App.tsx` now delegates Brain Home and Admin Console to feature modules.
- `ProductFlow.tsx` now orchestrates onboarding screen components instead of
  carrying inline screen markup and style rules.
- Model download consent now shows download size, local storage location,
  external target, and a do-later path before any download begins.
- Admin Console and onboarding copy use the shared i18n map for Korean/English
  coverage.
- `ltcai_cli.py`, `telegram_bot.py`, and `p_reinforce.py` are compatibility
  shims over package-owned modules.
- Tool and interaction router registration use typed context objects while
  preserving route order and public router factory compatibility.
- `npm run release:smoke` validates the generated wheel, npm tgz, static app
  assets, and Tauri artifacts after the exact-version artifact validator.

### Expected Artifacts

- `dist/ltcai-6.2.0-py3-none-any.whl`
- `dist/ltcai-6.2.0.tar.gz`
- `dist/ltcai-6.2.0.vsix`
- `ltcai-6.2.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_6.2.0_aarch64.dmg`

Package registry publish and production deploy remain owner-run steps.
