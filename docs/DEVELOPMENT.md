# Lattice AI Development

> **Status: canonical** — current contributor guidance, kept in sync with the
> current release.

Current release: **12.2.0 — Small Voice**.

This document is the contributor onboarding path: how to boot the tree, where
a change belongs, which gates will go red, and the house rules that keep the
product honest. Product positioning and the five-minute first-run stay in
[README.md](../README.md) and [ONBOARDING.md](ONBOARDING.md). Supported
release history is 11.0.0 and later in [CHANGELOG.md](CHANGELOG.md) and
[RELEASE.md](../RELEASE.md) (11.6.0 rebuilt the product server in Rust, so
[SECURITY.md](../SECURITY.md) supports only 11.x). Open gaps toward v12.0.0
live in [ROADMAP.md](ROADMAP.md) (reference, not canonical).

## Product Contract

Lattice AI is a local-first Digital Brain that keeps your knowledge durable
across any AI model.

Engineering work should preserve these boundaries:

- the Brain is the durable asset; models are replaceable voices;
- SQLite is the live local Brain store;
- Docker, cloud models, downloads, update checks and Brain Network are opt-in
  (the PostgreSQL scale/migration tooling and the Telegram bridge left the tree
  in 11.6.0 with the platform code that became the AI worker);
- import-only paths must not initialize MLX/GPU, write files, or make network
  calls;
- normal Brain use must stay separate from Admin/operator controls.

## 10-minute quickstart

Need Python 3.11+, Node, and a Rust toolchain (`cargo`). The product is
`lattice-host`; Python is the 20-route compute worker it supervises.

```bash
git clone <this-repo>
cd "Lattice AI"          # or whatever the directory is named

python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -e ".[dev]"

npm ci
npm start                # product. If PATH has a stale global LTCAI: node bin/ltcai.js
```

Then open:

```text
http://127.0.0.1:4825/app
```

The first screen is the wake step: language switcher (한국어 / English), the
Living Brain, and the Korean headline **「내 지식이 살아나는 Brain을 시작하세요.」**
with the primary **「Brain 지금 깨우기」**. An existing local Brain can skip
into the home canvas.

Apple Silicon local-model extras (MLX), only if you will load a model:

```bash
.venv/bin/pip install -e ".[local]"
```

### `npm start` vs `npm run dev`

| command | what it actually is | what you get |
| --- | --- | --- |
| `npm start` | the `LTCAI` bin → [`bin/ltcai.js`](../bin/ltcai.js) | **the product**: `lattice-host` on `127.0.0.1:4825`, supervising the worker, serving the SPA at `/app` |
| `node bin/ltcai.js` | the same bin, called directly | same product; use this if a stale global `LTCAI` is on `PATH` |
| `npm run dev` | `node scripts/run_python.mjs -m latticeai.cli.entrypoint --reload` | **not the product**. The 20-route worker with uvicorn `--reload`. Prints `Lattice AI worker on http://127.0.0.1:4825`. There is no SPA. |

`scripts/run_python.mjs` prefers `.venv/bin/python`. `bin/ltcai.js` prefers
`LTCAI_PYTHON`, then a managed `~/.ltcai/npm-python`, then `.venv`. Pin the
worker interpreter with `LTCAI_PYTHON` when both exist.

`bin/ltcai.js` looks for the host at `LATTICEAI_HOST_BIN` / `LTCAI_HOST`, then
`rust/target/release/lattice-host`, then `rust/target/debug/lattice-host`. If
none exist it runs `cargo build -p lattice-host` (debug). A stale
`rust/target/release/lattice-host` wins over a current debug binary — pin or
rebuild (`cargo build -p lattice-host --release`) if `-V` is not 12.2.0.

Do not run `npm start` and `npm run dev` on the same port. The worker
entrypoint defaults to `LATTICEAI_PORT` or **4825**, the same port as the
product. Iterate on worker compute with
`LATTICEAI_PORT=4835 npm run dev` (and `--no-spawn` / a pinned worker port on
the host if you are pairing them).

## The map — 어디에 무엇을

Two crate-local maps are the contract. This table is the index; they are the
detail. Read the matching `mod.rs` before adding a file.

- [`rust/lattice-agent/ARCHITECTURE.md`](../rust/lattice-agent/ARCHITECTURE.md)
  — `kernel/` decides; `parse/` `content/` `tools/` never import the kernel;
  `surface/` carries HTTP; `prompts/` fills a blank. Arrows point down only.
- [`rust/lattice-platform/ARCHITECTURE.md`](../rust/lattice-platform/ARCHITECTURE.md)
  — this crate **offers**. It does not decide whether a run is allowed
  (`lattice-agent` kernel) and it does not own what is true (`lattice-core`).

```text
lattice-host            one door — mounts every family
  lattice-platform      what the product offers
      workspaceos/      place, people, dials, workspace_os.json
      toolsurface/      MCP, tools, plugins, agents, computer-use
      governance/       review, proposals, automation, workflows, hooks
      knowledge/        export/import/backup, network, boundary, voice
      modelops/         catalog, recommendations, setup
      adminops/         audit, security dashboard, funnel
      shell/            SPA + 308 redirects
  lattice-agent         may it run, and what does it do
  lattice-core          what is true — GraphWriter is the only graph writer
  lattice-retrieval     search, memory, chronicle, command, evidence
  lattice-ingest        files, folders, browser capture
  lattice-jobs          embed drain + index API
  lattice-chat          POST /chat
  lattice-auth          who is this
Python worker           compute only — 19 allowlisted routes
frontend/src            SPA
```

Compatibility re-exports at each crate's `src/lib.rs` keep the pre-v12 paths
working for *consumers*. New code inside a crate uses the real path
(`crate::kernel::state`, `crate::adminops::admin`).

| 할 일 | 어디에 | 하지 말 것 |
| --- | --- | --- |
| **새 제품 API 라우트** | The `lattice-platform` domain that names the subject (`workspaceos/`, `toolsurface/`, `governance/`, `knowledge/`, `modelops/`, `adminops/`). One module owns one family: `MOUNTED: &[(&str, &str)]` + `router(state)`, then mount the factory in [`rust/lattice-host/src/gateway/product.rs`](../rust/lattice-host/src/gateway/product.rs). A **page shell** is a 308 in `shell/ui_redirects`, not a second handler — except `GET /plugins/sdk` (it carries `require_user`, so it is `toolsurface::plugins`). | Do not put retrieval/search/chronicle in platform — that is `lattice-retrieval`. Ingest doors are `lattice-ingest`. Chat is `lattice-chat`. Index drain is `lattice-jobs`. Identity is `lattice-auth`. Agent-loop HTTP is `lattice-agent` `surface/`. A handler here that re-derives "is this allowed" has forked `lattice_agent::kernel::permission`. A module that opens the graph has become a second writer. |
| **에이전트 툴 추가** | A tool that runs *inside an agent turn* goes in `lattice-agent` `tools/`, in the file named for what it *touches*: `files` (workspace), `vault` (Brain/Obsidian), `shell` (subprocesses), `desktop` (OS actuation), `render` (document creators), `scaffold` (templates), `local` (paths outside the workspace). Add the name to `tools::host::MUTATING_TOOLS` or `RENDER_TOOLS` and the arm to the dispatcher in [`tools/host.rs`](../rust/lattice-agent/src/tools/host.rs). `is_native` follows from those tables. Every path resolves through `tools::sandbox::Workspace::resolve`; every write passes `content::sanitize::sanitize_write_content`. HTTP exposure of an existing capability is `lattice-platform` `toolsurface/tools/` plus `tools::governance_for` — not a second implementation. | Do not add a permission check in the handler — the kernel already said yes. Do not `std::fs` a raw path. Do not put a read-only compute handler here; those stay on the worker (`POST /agent/tool`). Pointer tools (`computer_*` click/move/type/…) stay on the worker on purpose. |
| **파싱 러그 추가** | `lattice-agent` `parse/action.rs` (or `parse/channel.rs` for a new model-family frame). Append it to the ordered chain — cheapest and most literal first — and **name it in the returned `repairs` list**. CPython primitives (`pyjson`, `pyliteral`, `pyshlex`, `pystr`) are pinned by [`rust/fixtures/agent/`](../rust/fixtures/agent/FROZEN.md). New answers that Python never gave live under **new keys** (see [`agent_loop/FROZEN.md`](../rust/fixtures/agent_loop/FROZEN.md) `*_extended`). | A silent repair is a bug. Never supply a tool name, path, or argument the model did not write. Do not put a decision here (that is `kernel/`) or file-body salvage (that is `content/`). Do not rewrite a frozen row in place. |
| **새 워커 계산** | Compute only — embed, parse, extract, render, ASR, LLM. Add the handler next to its family (`latticeai/api/worker_compute.py` for the `/worker/*` seams, or `WORKER_ROUTES` / `WORKER_SEAM_ROUTES` in [`latticeai/runtime/build_phases/worker_profile.py`](../latticeai/runtime/build_phases/worker_profile.py) if it is a model/engine door). Append `(method, path)` to the matching tuple. Regenerate the committed projection: `.venv/bin/python scripts/gen_worker_allowlist_fixture.py`. `tests/unit/test_worker_allowlist.py` pins the file; `lattice-host` `include_str!`s [`rust/fixtures/worker_allowlist.json`](../rust/fixtures/worker_allowlist.json). There are **19** routes. Anything not native and not on that list is `404`. | Do not add a worker route for a product feature. The worker stores nothing — no graph, no sessions, no files. `latticeai.app_factory.build_context` is the **worker** composition root (`create_worker_app`); it is not the product. Import of `latticeai.app_factory` must stay free of MLX/GPU init, singleton construction, filesystem writes, and network calls. |
| **프론트 화면 / 패널** | Route screens in `frontend/src/pages/` (`Brain`, `Capture`, `Chronicle`, `Act`, `Library`, `System`). Feature panels in `frontend/src/features/<area>/`. Shared chrome in `frontend/src/components/`. Hash routes and aliases in `frontend/src/routes.ts`. Copy in `frontend/src/i18n/`: one namespace module calls `registerCopy` (`shell` is eager; `brain`, `workspace`, `onboarding`, `chronicle` register from the lazy route). Author Korean first; ship the English pair in the same module. `t()` falls back to `COPY.ko`. Colors from [`frontend/src/styles/tokens.css`](../frontend/src/styles/tokens.css) as `hsl(var(--token))`; surface rules in the narrowest file under `frontend/src/styles/experience/`. Brain behavior belongs in `useBrainChat` / `useBrainHistory` / `useBrainIngestion` / `useBrainProof`. Failed `ApiResult` stays an unavailable/error state. Colocate a Vitest file; the floor is 100% `all: true`. | No user-facing English/Korean literals in TSX (`scripts/check_i18n_literals.mjs`). Do not import a namespace you do not register (`check:i18n-namespaces`). Do not add a competing shell rule to `styles.css`. Do not put runtime metrics or admin tools on the default first screen. Regenerate `frontend/src/api/openapi.ts` with `npm run frontend:openapi` when a contract changes — do not edit it by hand. |
| **지식 그래프 쓰기** | [`lattice_core::graph_write::GraphWriter`](../rust/lattice-core/src/graph_write/mod.rs) only. Public doors: `open` / `with_parts`; ingest `ingest_file` / `ingest_content` / `ingest_message` / `ingest_event`; vectors `write_vectors` / `write_vectors_with` / `rebuild_vector_index`; curation `curate` / `curate_scan` / `curate_noise` / promotions / `mark_superseded`; documents `delete_document_tree` / `delete_node` / `set_node_sensitivity` / `stamp_node_validity` / `set_local_source_watch` / `remove_local_source` / `upsert_nodes` / `upsert_edges`; provenance `record_ingestion` / `import_graph_data`. Production `open()` stamps `"personal"` when a write omits a workspace. Chunks, concepts, triples and embeddings arrive as *data* on the request types — the writer never calls a model. | Do not open `knowledge_graph.sqlite` for writing from another crate. Do not go through the retired `/worker/graph/mutate`. `with_parts` leaves `default_workspace` unset so frozen goldens keep their legacy-null rows — that constructor is for parity, not the product. |
| **검증 / 골든** | Each family has a `FROZEN.md` stating what wrote it and that it must not be regenerated. **Agent kernel** — [`rust/fixtures/agent/`](../rust/fixtures/agent/FROZEN.md), `lattice-agent` `tests/parity.rs`. **Agent loop** — [`rust/fixtures/agent_loop/`](../rust/fixtures/agent_loop/FROZEN.md), `tests/agent_loop.rs`. **Retrieval / embeddings** — [`rust/fixtures/golden/`](../rust/fixtures/golden/FROZEN.md), `lattice-retrieval` + `lattice-core` `tests/golden_embeddings.rs`. **Graph writes** — [`rust/fixtures/graph_write/`](../rust/fixtures/graph_write/FROZEN.md). **Chunking** — [`rust/fixtures/chunking/`](../rust/fixtures/chunking/FROZEN.md), `lattice-ingest`. **HTTP replay** — [`rust/fixtures/http/`](../rust/fixtures/http/FROZEN.md). Python worker: `npm run test:unit`. SPA: `npm run test:frontend`. Visual: `npm run test:visual`. Integration starts its own loopback host with disposable HOME/data and refuses non-loopback URLs (`npm run test:integration`). | Frozen expected values are inviolable. If the port disagrees, **the port changed** — fix the port. A deliberate new answer goes under a **new key** (or a new file) and is named as not-parity; do not edit a row in place. Do not resurrect a deleted generator to "refresh" a golden. |

A new gate or verdict goes in `lattice-agent` `kernel/` and fails **closed**:
unknown is a refusal, an unreachable verifier is `NEEDS_REVIEW`, a policy-less
tool is its most dangerous plausible class. Gate order is pinned by the agent
goldens; reordering is a behaviour change.

A new built-in prompt goes in `prompts/mod.rs` as a constant plus the test that
feeds it through the real parser. A caller-supplied prompt always wins.

`workspace_os.json` has exactly one writer:
`workspaceos::workspace::store::WorkspaceOsStore`. Governance's
`GovernanceState` is a facade over the same `Arc`. Audit lines go through
`adminops::admin::append_audit_event` only.

## The gates

Run the smallest affected gate while iterating. Before committing broad
runtime, API, UI, or release work:

```bash
npm run lint
npm run typecheck
npm run test:frontend
npm run test:unit
npm run docs:check-links
npm run rust:lint          # fmt --check + clippy -D warnings
npm run rust:test
```

Use these when the change touches the relevant surface:

```bash
npm run test:integration
npm run test:visual
npm run test:coverage
npm run test:frontend:coverage
npm run desktop:tauri:check
npm run release:evidence
npm run release:artifacts
npm run release:validate
```

### `npm run lint` — ten members, in this order

1. `lint:python` — `ruff check .` then `mypy`. Fix ruff locally (`ruff check --fix`) before arguing with the rule; mypy errors name the module under `latticeai/` or `lattice_brain/`.
2. `lint:visual` — `node --check` on every `tests/visual/*.js`, `*.cjs`, and `tests/visual/mock_server/*.cjs`. A syntax error in a screenshot spec fails here, not in Playwright.
3. `lint:frontend` — `scripts/lint_frontend.mjs`: `tsc --noEmit` plus static checks over `frontend/` and `static/app`.
4. `frontend:openapi:check` — `frontend/openapi.json` and `frontend/src/api/openapi.ts` must match a fresh isolated export. Fix: `npm run frontend:openapi` and commit both artifacts.
5. `scripts/check_i18n_literals.mjs` — no user-facing string literals in `frontend/src` TS/TSX. Move the words into the matching `frontend/src/i18n/` namespace (both `ko` and `en`).
6. `check:i18n-namespaces` — a lazy chunk may only read keys whose namespace it imports. Import the namespace module from the route, do not reach into a sibling dictionary.
7. `check:bundle` — initial JS gzip closure stays under 150 KiB. Heavy UI belongs behind `React.lazy`. Do not raise the budget to hide a sync import.
8. `check:server-i18n` — routers listed in `scripts/check_server_i18n.mjs` must raise through `http_error` / `translate`, never a quoted `detail=`. Adding a name to `LOCALIZED` is how a migration is declared finished. Today it locks 4 worker routers and reports 2 unclaimed (`health`, `search`).
9. `scripts/check_release_evidence_bound.mjs` — see the binding paragraph below.
10. `check:max-file-lines` — see the line-limit paragraph below.

Three things left the local chain in 11.8.0 and must not be "restored" as
habits: `check:python` (ruff already parses every file on every CI test leg),
`check:legacy-debt` (the Python test is authoritative), and the extension
tests (CI invokes them directly on the 3.11 + ubuntu leg). See
[CI_AND_RELEASE_GATES.md](CI_AND_RELEASE_GATES.md).

### Coverage floors

The floors are not symmetrical, and that is deliberate. Python
(`[tool.coverage.report] fail_under = 90` in `pyproject.toml`) is a
**line-coverage** floor; branch measurement is off. Run
`npm run test:coverage`. A red report is uncovered product lines in
`latticeai/` or `lattice_brain/` — add a unit test, do not lower the floor
and do not hide the line with `pragma: no cover` unless the exclusion is
reasoned. The frontend (`vitest.config.ts`) pins **100% on statements,
branches, functions, and lines** with `all: true`, so an untested new module
is in the denominator. Run `npm run test:frontend:coverage`. A file that
vanishes from the text reporter is fully covered; confirm with
`--coverage.reporter=json-summary`. `frontend/src/i18n/**`,
`frontend/src/api/openapi.ts`, and `frontend/src/main.tsx` are excluded on
purpose.

### Max-file-lines (1,000)

`scripts/check_max_file_lines.mjs` fails any git-tracked
`*.py *.ts *.tsx *.js *.mjs *.cjs *.css *.rs` over 1,000 lines, tests
included. It exists because a 10k-line CSS file and twenty-seven other
oversize modules landed before anyone noticed — long files burn review and
conflict on every feature. Split by cohesion, not at line 1,000. Exempt only
generated paths (`frontend/src/api/openapi.ts`, `static/app/`,
`static/vendor/`, `src-tauri/gen/`). A new exemption for a hand-written file
is not a fix.

### Clippy `-D warnings`

`npm run rust:lint` is `cargo fmt --all -- --check` then
`cargo clippy --workspace --all-targets -- -D warnings` inside `rust/`. CI
runs the same. The workspace no longer carries blanket `#![allow]`. Fix the
diagnostic at the source; do not re-allow it. Format drift is `cargo fmt`
in `rust/`.

### Docs gates

`npm run docs:check-links` is two scripts:
`scripts/check_markdown_links.mjs` (README plus one hop of README-linked
Markdown) and `scripts/check_doc_status.mjs` (every relative link under the
root canonical docs and `docs/**`). A red line is a broken relative path —
fix the href or put the file where the href says. Canonical docs (this one
included) must carry `Current release: **12.2.0 — …**` matching
`package.json`; do not bump that marker here ahead of the release lane.
`npm run docs:check-current` is the release-set version pin
([`scripts/check_current_release_docs.mjs`](../scripts/check_current_release_docs.mjs)).
Reference docs ([ROADMAP.md](ROADMAP.md), this file's siblings with
`Status: reference`) are not held to the version marker. Historical
changelog entries that mention old versions stay.

### Release-evidence binding

`scripts/check_release_evidence_bound.mjs` (lint member 9) checks that
`output/release/v12.2.0/SCREENSHOT_INDEX.md` still names the live
`static/app/asset-manifest.json` digest and the visual mock-server
fingerprint. It exists so a later `npm run build:assets` cannot ship
screenshots of an older UI, and so a mock-server edit cannot leave the
evidence describing a panel the tree no longer produces. Fix: run
`npm run build:assets` then `npm run release:evidence` (needs Playwright
Chromium and `ffmpeg`); the capture writes only to
`output/release/vX.Y.Z/`. `LTCAI_SKIP_RELEASE_EVIDENCE_BOUND=1` is the
capture script's own hatch, not a commit-time escape.

## House rules

**Honesty.** A stub is a stub. A capability that cannot complete surfaces
`unavailable` / `skipped` / `simulation` / `awaiting_approval` with a reason
— never a fabricated success, score, or record. A remaining `—` in
[FEATURE_STATUS.md](../FEATURE_STATUS.md) or
[SURFACE_PARITY.md](SURFACE_PARITY.md) states why it is a design boundary.
Do not write "Current" over a surface that 404s. See
[PROJECT_PRINCIPLES.md](../PROJECT_PRINCIPLES.md).

**Fail-closed verification is law.** An unverifiable agent outcome is
`NEEDS_REVIEW`, not success. An unknown permission case is a refusal. A
policy-less tool takes its most dangerous plausible class. The worker
allowlist answers `404` for everything it does not name. Graph scope errors
do not silently widen. Change-proposal approval whose base SHA no longer
matches is a **409**, never a merge and never a silent overwrite. Additive
creates may run with minimal friction; mutations and deletions of existing
user files are staged as review proposals (`change_proposal`) and applied
exactly as reviewed.

**Korean UX is the default.** Author product copy in Korean first; ship the
English pair in the same `registerCopy` table. `t()` falls back to
`COPY.ko`. The first-run `LanguageChooser` is on every onboarding step.
Do not leave an English literal on a path a Korean reader will see — the
i18n gates exist to catch that, and the remaining English API `detail`
strings are a named gap in [ROADMAP.md](ROADMAP.md), not a style.

**Tokens-only CSS.** React color lives in `frontend/src/styles/tokens.css`
and is consumed as `hsl(var(--token))`. No hex on a themed surface. Put a
new rule in the narrowest `frontend/src/styles/experience/` file. Do not
add another competing shell or composer rule to `styles.css`. No glass —
the 11.7.0 elevation ladder is the vocabulary.

**Workspace `null` = personal.** `DEFAULT_WORKSPACE_ID` is `"personal"`.
Reads treat a NULL or blank `workspace_id` as personal visibility.
Production `GraphWriter::open` stamps `"personal"` when a write omits a
workspace. A request that names two different workspaces (header vs.
query/body) is 403. Do not invent a second default string, and do not
treat NULL as "visible to every tenant".

## Documentation sync

For user-facing, API, runtime, release, or packaging changes, check:

- [README.md](../README.md)
- [ARCHITECTURE.md](../ARCHITECTURE.md)
- [FEATURE_STATUS.md](../FEATURE_STATUS.md)
- [RELEASE.md](../RELEASE.md)
- [CHANGELOG.md](CHANGELOG.md)
- [SECURITY.md](../SECURITY.md) when trust/security changes
- [vscode-extension/README.md](../vscode-extension/README.md) when editor
  integration changes
- [LEGACY_COMPATIBILITY.md](LEGACY_COMPATIBILITY.md) when root compatibility
  files change
- this file, when the contributor path or the map changes
- [ROADMAP.md](ROADMAP.md), when a named gap closes or a new honest leftover
  appears

The release-docs lane owns README / ARCHITECTURE / FEATURE_STATUS version
bumps. Do not move this file's `Current release` marker off 12.2.0 ahead of
that lane.

Release/publish examples must use exact target-version filenames. Do not
document wildcard artifact upload commands.

For 12.2.0 release work, exact artifacts are:

- `dist/ltcai-12.2.0-py3-none-any.whl`
- `dist/ltcai-12.2.0.tar.gz`
- `ltcai-12.2.0.tgz`
- `dist/ltcai-12.2.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_12.2.0_aarch64.dmg`

The dmg is ad-hoc signed (effectively unsigned); `npm run release:validate`
checks the names and presence, not a Developer ID signature.
