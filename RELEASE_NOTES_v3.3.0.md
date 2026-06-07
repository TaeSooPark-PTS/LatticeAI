# Lattice AI v3.3.0

**Product-quality & honesty release.** No new product areas. The focus is
verifying what actually works, removing misleading states, fixing real blockers,
and making the system truthful and maintainable. Every change is evidence-based
(source-traced); the full audit is in
[FEATURE_STATUS.md](FEATURE_STATUS.md) and the design system in
[STYLE_SYSTEM.md](STYLE_SYSTEM.md).

## Functional audit

All 18 feature areas were audited UI → API → backend and classified
WORKING / PARTIAL / PLACEHOLDER / DISABLED / BROKEN with `file:line` evidence
(see `FEATURE_STATUS.md`). Headline result: the `/app` SPA is already unusually
honest — the API adapter never fabricates data, reporting `live` vs `unavailable`
everywhere. Most surfaces are WORKING or honestly DISABLED. This release fixes the
handful of real gaps the audit found.

## Honest UI state changes

- **Chat grounding chips** relabeled to describe what they actually do (filter the
  retrieval-context preview); they do not gate generation, so the tooltip no
  longer implies they do.
- **Memory view** no longer claims prune/clear controls it does not surface, and
  its recall copy reflects the workspace + graph scope it actually searches (not
  "every tier").
- **Files** keeps the honest "Connecting a folder requires the Lattice desktop
  local agent — not available in this build" state for folder watching, now
  alongside a working manual upload (below).

## Version consistency

Single source of truth at **3.3.0** across runtime constants
(`WORKSPACE_OS_VERSION`, `__version__`, `MARKETPLACE_VERSION`,
`MULTI_AGENT_VERSION`), `pyproject.toml`, `package.json`, the VS Code extension
manifest, both lockfiles, and the generated v3 asset manifest. The build manifest
now derives its version from `package.json`, and **Settings → About reads the live
version from `/health`** instead of a hard-coded string (this is what let a stale
`v3.1.0` linger in the UI). Guarded by `tests/unit/test_version_consistency.py`.

## Manual file upload (new working path)

The Files drop zone was previously decorative. It is now a real uploader —
drag-and-drop or file picker — wired to the existing
`POST /upload/document` pipeline (parse → chunk → embed → knowledge-graph
ingest). Accepts PDF · DOCX · XLSX · PPTX · TXT · MD · CSV, ≤10 MB each. Folder
connection remains honestly disabled (it needs the desktop local agent, not in
this build).

## Chat SSE parsing fix

The v3 chat stream parser only handled the standard `chunk` event, so
document-generation responses (which stream a `text` event shape) rendered a
false "Couldn't reach the model" error even though the backend generated and
saved the document. The parser now accepts both `chunk` and `text`.

## Home retrieval status fix

`/api/index/status` is vector-centric and emits no `pipelines` key, but the Home
pillars and topbar chip expect one — so a live, indexed backend always showed a
false "Retrieval status unavailable". The adapter now synthesizes the pipelines
shape from the real index status (vectors) plus the KG stats endpoint (entities),
without fabricating numbers.

## Vercel deployment truth finding

Inspection via the Vercel MCP found the `lattice-ai` project's single production
deployment **returns HTTP 500 on every route** (`could not import "server.py"`),
with `project.live = false`. Lattice AI is **local-first** (it needs local MLX on
Apple Silicon, a local filesystem, and a local SQLite knowledge graph) and is
**not a valid production runtime on Vercel serverless**. No Vercel URL should be
presented as a working product. Run Lattice AI locally (`LTCAI` / `npm start`).

## Known limitations

- **Vercel:** the hosted deployment is non-functional (HTTP 500); do not treat any
  Vercel URL as a product surface.
- **Hooks are registry-only:** hooks can be registered, ordered, enabled, and
  inspected, but no runtime dispatch site exists yet — registered hooks are not
  actually fired during runs/tools/workflows. Flagged in `FEATURE_STATUS.md`;
  dispatch is deferred to a future release rather than faked.
- **Uploaded documents** are ingested into the knowledge graph (searchable in Chat
  and Hybrid Search) but do **not** appear in the Files table, because upload
  creates Document/Chunk nodes rather than a `knowledge_sources` row. The upload
  success toast states this. Unifying the two stores is a future improvement.
- **Local model inference** requires Apple Silicon + the optional `mlx-vlm`
  extra; without it, chat honestly reports `no_model_loaded` (cloud OpenAI-compatible
  models work with a key).
- The default multi-agent runner is deterministic and LLM-free by design (it
  reports completed steps; it does not call a model).

## Verification

Run locally and in CI before release:

- `npm run lint` — 64/64 v3 frontend modules pass
- `node scripts/build_v3_assets.mjs` — asset manifest at 3.3.0
- `npm run check:python` — py_compile of all backend modules
- `pytest tests/unit` — **371 passed** (incl. new `test_version_consistency.py`)
- `python -m build` + `twine check dist/*` — both artifacts PASSED, wheel
  METADATA `Version: 3.3.0`
- `python scripts/validate_release_artifacts.py 3.3.0 --require-vsix --require-tgz` — OK
- Integration tests run in CI against a started server (they require a live
  backend; not run standalone).

## Artifacts

| Target | File |
| --- | --- |
| PyPI wheel | `ltcai-3.3.0-py3-none-any.whl` |
| PyPI sdist | `ltcai-3.3.0.tar.gz` |
| npm tarball | `ltcai-3.3.0.tgz` |
| VS Code / Open VSX | `ltcai-3.3.0.vsix` |

> Package-store publication (PyPI / npm / VS Code Marketplace / Open VSX) is
> intentionally manual and was **not** performed by this release; artifacts are
> built and validated as publish-ready. The `Release` CI workflow rebuilds and
> validates these on the `v3.3.0` tag but never auto-publishes.
