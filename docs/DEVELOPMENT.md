# Lattice AI Development

> **Status: canonical** — current contributor guidance, kept in sync with the
> current release.

Current release: **11.3.0 — Time Remembers**.

This document is for contributors working on the local-first Digital Brain
codebase. Product positioning and quick start stay in `README.md`; release
history is intentionally limited to 8.0.0-9.9.0 in `docs/CHANGELOG.md` and
`RELEASE.md`.

## Product Contract

Lattice AI is a local-first Digital Brain that keeps your knowledge durable
across any AI model.

Engineering work should preserve these boundaries:

- the Brain is the durable asset;
- models are replaceable voices;
- SQLite is the live local Brain store;
- PostgreSQL scale/migration tooling, Docker, cloud models, downloads, update checks, Telegram, and
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
npm run lint
npm run typecheck
npm run test:frontend
npm run test:unit
npm run docs:check-links
```

`npm run lint` runs the Python Ruff baseline, frontend TypeScript lint gate,
visual smoke syntax checks, an exact generated-OpenAPI drift check, i18n literal
checks, and browser-extension syntax/behavior tests.

Use these when the change touches the relevant surface:

```bash
npm run test:integration
npm run test:visual
npm run desktop:tauri:check
npm run release:evidence
npm run release:artifacts
npm run release:validate
```

Run `npm run build:assets` before `npm run release:evidence`. The capture command
reads the current package version, writes only to `output/release/vX.Y.Z/`, and
requires Playwright Chromium plus `ffmpeg` for the checked-in GIF/WebM evidence.

`npm run test:integration` starts its own loopback server with disposable HOME,
data, Brain, agent, XDG, temp, SQLite, and vault paths. It refuses non-loopback
external base URLs so validation cannot mutate a developer's real local Brain.

Regenerate committed API artifacts with `npm run frontend:openapi`; CI and
`npm run lint` fail when either `frontend/openapi.json` or
`frontend/src/api/openapi.ts` differs from a fresh isolated export.

## Frontend Experience Ownership

`frontend/src/styles/tokens.css` owns React color tokens and
`frontend/src/styles/experience.css` imports the focused shell, conversation,
graph, capture, and responsive layers under `frontend/src/styles/experience/`.
Keep ownership in the narrowest surface file; do not add another competing
shell or composer rule to `styles.css`.

Brain behavior belongs in the focused `useBrainChat`, `useBrainHistory`,
`useBrainIngestion`, and `useBrainProof` hooks. Translation namespaces live in
`frontend/src/i18n/`. Failed `ApiResult` values must remain unavailable/error
states rather than being normalized to healthy empty data, and affected paths
must have Vitest coverage.

Default mode should expose user tasks and outcomes. Runtime metrics, registry
identifiers, pipeline controls, and administrator tools belong in progressive
disclosure or advanced/admin mode. New navigation and tabs must preserve
keyboard access, visible focus, mobile safe areas, and reduced-motion behavior.

## Runtime Assembly

`latticeai.app_factory` is the composition root. Keep it import-safe:

- no MLX/GPU init at module import time;
- no singleton construction at module import time;
- no filesystem writes at module import time;
- no external network calls at module import time.

Runtime assembly seams live under `latticeai.runtime` as typed stages:

- `config_runtime.py` derives immutable app config values from `Config`;
- `security_runtime.py` applies trusted proxy/security-derived settings;
- `brain_runtime.py` constructs Brain Core and conversation primitives;
- model, platform, and router stages receive explicit dependencies and return
  typed bundles rather than ambient `locals()` maps.

The module-level `server_app` compatibility surface is an explicit allowlist.
Model services use injected state, and HTTP exceptions belong at the API
boundary. Readiness tests should reject forbidden patterns instead of treating
symbol presence alone as architectural completion.

Future extraction should continue with AgentRuntime, ToolRegistry, config,
server decomposition, and Knowledge Graph stabilization in that order when
architectural debt is present.

## Runtime Hook Coverage

Knowledge Graph ingestion paths must continue to pass through the shared
pre-tool/post-tool lifecycle:

- browser `read-url` ingestion dispatches `tool.kg_ingest.*` events;
- browser `ingest-current-tab` ingestion dispatches the same lifecycle;
- `IngestionPipeline` remains the common ingestion boundary behind those routes.

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

For 9.9.0 release work, exact artifacts are:

- `dist/ltcai-9.9.0-py3-none-any.whl`
- `dist/ltcai-9.9.0.tar.gz`
- `ltcai-9.9.0.tgz`
- `dist/ltcai-9.9.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_9.9.0_aarch64.dmg`
