# Lattice AI v6.3.0 - Product Hardening Completion

Lattice AI v6.3.0 completes the hardening pass on top of the v6.2 product
decomposition line. It keeps the local-first Digital Brain direction stable
while tightening the remaining Brain archive, memory provenance, document
ingestion, Review Center, model runtime, backend wiring, compatibility shim,
i18n, and release validation surfaces.

## Highlights

- Brain archive UX now has clearer passphrase guidance, inspect/restore preview
  summaries, and portability status language.
- Memory provenance and document ingestion surfaces show source type, created
  time, source metadata, retry/failure details, and Brain/Graph routing context.
- Review Center Run Now remains preview/regenerate rather than approval, and
  backend tests now verify scoped runner context, unchanged status, and run id
  back-linking.
- Review Center run-now runtime wiring moved out of `app_factory.py` into
  `latticeai.runtime.review_wiring`.
- Legacy root shim smoke tests now cover `server.py`, `knowledge_graph.py`,
  `ltcai_cli.py`, `telegram_bot.py`, and `p_reinforce.py`.
- Package/runtime/static metadata is synchronized to `6.3.0`.

## Expected Artifacts

- `dist/ltcai-6.3.0-py3-none-any.whl`
- `dist/ltcai-6.3.0.tar.gz`
- `dist/ltcai-6.3.0.vsix`
- `ltcai-6.3.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_6.3.0_aarch64.dmg`

## Validation Scope

- Python/unit validation for Review Center run-now contract and root shim
  compatibility paths.
- Frontend typecheck, lint, unit, and visual smoke coverage for the v6.3 product
  hardening branch.
- Release artifact validation and smoke checks for exact-version wheel, sdist,
  npm tgz, VSIX, static asset manifest, and Tauri DMG outputs.

Package registry publish, production deploy, tag creation, and GitHub Release
publication remain owner-run follow-up steps.
