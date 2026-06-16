# Lattice AI v6.3.1 - Access Runtime / i18n Follow-up

Lattice AI v6.3.1 is a focused follow-up to the v6.3.0 product-hardening
release. It continues reducing `app_factory.py` debt and closes remaining
Capture/Review Center localization gaps without changing the core user flows.

## Highlights

- Access-control closures moved from `app_factory.py` into
  `latticeai.runtime.access_runtime`.
- Role lookup, bearer/cookie token extraction, authenticated user checks,
  admin checks, and public user projection keep their existing contracts.
- New unit tests cover admin/user/unauthenticated behavior and identity
  projection for the extracted runtime helper.
- Capture and Review Center copy now use shared Korean/English i18n keys for
  tabs, headings, placeholders, actions, empty states, and feedback.
- Package/runtime/static metadata is synchronized to `6.3.1`.

## Expected Artifacts

- `dist/ltcai-6.3.1-py3-none-any.whl`
- `dist/ltcai-6.3.1.tar.gz`
- `dist/ltcai-6.3.1.vsix`
- `ltcai-6.3.1.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_6.3.1_aarch64.dmg`

Package publish and production deployment remain owner-run only.
