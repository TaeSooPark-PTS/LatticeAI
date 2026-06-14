# Lattice AI v5.1.0 - Product Trust & Clarity Release

v5.1.0 clarifies Lattice AI as a private AI memory layer and hardens the trust
boundary around local files, secrets, CSP, model downloads, and Brain ownership.

## Product Message

- README first screen now opens with: "Your private AI memory layer. Keep your
  knowledge. Switch any model."
- Korean positioning is included directly: "모델은 바꿔도, 내 지식은 남는 로컬 AI 브레인."
- Practical use cases explain why a user should choose Lattice AI before
  implementation details.

## Security And Privacy

- Tauri production CSP is no longer `null`.
- `/app` static shell responses include a production CSP header.
- Secret redaction is centralized through shared security helpers and applied to
  logging, audit payloads, security exports, and builtin hook packets.
- Chat auto file read no longer reads arbitrary local paths from message text.
- `/engines/pull-model` requires explicit `allow_download=true`.

## Architecture

- `app_factory.py` now exposes builder seams for config, security, and Brain
  runtime construction.
- Brain Core wiring imports `set_llm_router` from `lattice_brain` internals
  instead of the deprecated root `knowledge_graph` shim.
- Brain archive restore now tolerates transient SQLite `-wal` / `-shm` siblings
  disappearing during checkpoint, removing a restore-time TOCTOU race.
- `npm run test:integration` now starts a local uvicorn server, waits for
  `/health`, runs integration tests, and shuts the server down.
- Release artifact cleanup now removes stale historical `dist/ltcai-*` and root
  `ltcai-*.tgz` files before rebuilding exact v5.1.0 artifacts.
- The `pts_claudecode` Discord bridge avoids bot-to-bot busy-reply loops while
  still allowing explicit collaboration mentions.

## Trust Documentation

- Added `PRIVACY.md`.
- Added `docs/WHY_LATTICE.md`.
- Added `docs/TRUST_MODEL.md`.
- Updated README, ARCHITECTURE, SECURITY, FEATURE_STATUS, and CHANGELOG for
  v5.1.0.

## Expected Artifacts

- `dist/ltcai-5.1.0-py3-none-any.whl`
- `dist/ltcai-5.1.0.tar.gz`
- `dist/ltcai-5.1.0.vsix`
- `ltcai-5.1.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_5.1.0_aarch64.dmg`
