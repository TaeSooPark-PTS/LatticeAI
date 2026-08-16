# Lattice AI Development

> **Status: canonical** — current contributor guidance, kept in sync with the
> current release.

Current release: **11.9.0 — Working Order**.

This document is for contributors working on the local-first Digital Brain
codebase. Product positioning and quick start stay in `README.md`; supported
release history is 11.0.0 and later in `docs/CHANGELOG.md` and `RELEASE.md`
(11.6.0 rebuilt the product server in Rust, so `SECURITY.md` supports only
11.x).

## Product Contract

Lattice AI is a local-first Digital Brain that keeps your knowledge durable
across any AI model.

Engineering work should preserve these boundaries:

- the Brain is the durable asset;
- models are replaceable voices;
- SQLite is the live local Brain store;
- Docker, cloud models, downloads, update checks and Brain Network are opt-in
  (the PostgreSQL scale/migration tooling and the Telegram bridge left the tree
  in 11.6.0 with the platform code that became the AI worker);
- import-only paths must not initialize MLX/GPU, write files, or make network
  calls;
- normal Brain use must stay separate from Admin/operator controls.

## Local Setup

```bash
npm install
npm start
```

`npm start` (or `bin/ltcai.js` / the `LTCAI` binary) is the product: it
boots `lattice-host` and serves the SPA at:

```text
http://127.0.0.1:4825/app
```

`npm run dev` is **not** the product. It starts the 19-route Python AI
worker with `--reload` (`latticeai.cli.entrypoint`). Use it when iterating
on worker compute seams, not to serve the app.

Apple Silicon local model extras:

```bash
pip install "ltcai[local]"
```

## Validation

Run the smallest affected gate while iterating. Before committing broad runtime,
API, UI, or release work, run:

```bash
npm run lint
npm run typecheck
npm run test:frontend
npm run test:unit
npm run docs:check-links
```

`npm run lint` is **ten** gates since 11.8.0, in order: `lint:python`
(ruff + mypy), `lint:visual`, `lint:frontend`, `frontend:openapi:check`,
`check_i18n_literals.mjs`, `check:i18n-namespaces`, `check:bundle`,
`check:server-i18n`, `check_release_evidence_bound.mjs`, and
`check:max-file-lines`. Three left in 11.8.0: `check:python` (ruff already
parses every file on every CI test leg), `check:legacy-debt` (the mjs mirror had
drifted from the Python test that states the same rule — the Python test is
authoritative), and the extension tests, which CI now invokes directly on the
3.11 + ubuntu leg.

Coverage floors are not symmetrical, and the difference is deliberate rather
than an oversight: **Python is a line-coverage floor of 90**
(`[tool.coverage.report] fail_under = 90`, branch measurement off) and the
frontend still pins **100% on all four vitest metrics**. Run
`npm run test:coverage` / `npm run test:frontend:coverage` to see either.

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
- the native ingest door in `lattice-ingest` (writing through `graph_write`)
  remains the single ingestion boundary behind those routes.

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

For 11.9.0 release work, exact artifacts are:

- `dist/ltcai-11.9.0-py3-none-any.whl`
- `dist/ltcai-11.9.0.tar.gz`
- `ltcai-11.9.0.tgz`
- `dist/ltcai-11.9.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_11.9.0_aarch64.dmg`

The dmg is ad-hoc signed (effectively unsigned); `npm run release:validate`
checks the names and presence, not a Developer ID signature.
