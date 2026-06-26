# Lattice AI Development

Current release: **8.1.0 - Intuitive Brain Home**.

This document is for contributors working on the local-first Digital Brain
codebase. Product positioning and quick start stay in `README.md`; release
history is intentionally limited to 7.0.0-8.1.0 in `docs/CHANGELOG.md` and
`RELEASE.md`.

## Product Contract

Lattice AI is a local-first Digital Brain that keeps your knowledge durable
across any AI model.

Engineering work should preserve these boundaries:

- the Brain is the durable asset;
- models are replaceable voices;
- SQLite is the default local store;
- PostgreSQL, Docker, cloud models, downloads, update checks, Telegram, and
  Brain Network are opt-in;
- import-only paths must not initialize MLX/GPU, write files, or make network
  calls;
- normal Brain use must stay separate from Admin/operator controls.

## Local Setup

```bash
npm install
npm run dev
```

The local app is served through the FastAPI sidecar at:

```text
http://127.0.0.1:4825/app
```

Apple Silicon local model extras:

```bash
pip install "ltcai[local]"
```

## Validation

Run the smallest affected gate while iterating. Before committing broad runtime,
API, UI, or release work, run:

```bash
npm run check:python
node scripts/run_python.mjs -m ruff check .
npm run lint
npm run typecheck
npm run test:unit
npm run docs:check-links
```

Use these when the change touches the relevant surface:

```bash
npm run test:integration
npm run test:visual
npm run desktop:tauri:check
npm run release:artifacts
npm run release:validate
```

## Runtime Assembly

`latticeai.app_factory` is the composition root. Keep it import-safe:

- no MLX/GPU init at module import time;
- no singleton construction at module import time;
- no filesystem writes at module import time;
- no external network calls at module import time.

Runtime assembly seams live under `latticeai.runtime`:

- `config_runtime.py` derives app config values from `Config`;
- `security_runtime.py` applies trusted proxy/security-derived settings;
- `brain_runtime.py` constructs Brain Core and conversation primitives.
- model/runtime, ToolRegistry/MCP, router, static asset, and lifespan seams
  should continue moving out of monolithic app-factory helpers.

Future extraction should continue with AgentRuntime, ToolRegistry, config,
server decomposition, and Knowledge Graph stabilization in that order when
architectural debt is present.

## Documentation Sync

For user-facing, API, runtime, release, or packaging changes, check:

- `README.md`
- `ARCHITECTURE.md`
- `FEATURE_STATUS.md`
- `RELEASE.md`
- `docs/CHANGELOG.md`
- `SECURITY.md` when trust/security changes
- `vscode-extension/README.md` when editor integration changes
- `docs/LEGACY_COMPATIBILITY.md` when root compatibility files change

Release/publish examples must use exact target-version filenames. Do not
document wildcard artifact upload commands.

For 8.1.0 release work, exact artifacts are:

- `dist/ltcai-8.1.0-py3-none-any.whl`
- `dist/ltcai-8.1.0.tar.gz`
- `ltcai-8.1.0.tgz`
- `dist/ltcai-8.1.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_8.1.0_aarch64.dmg`
