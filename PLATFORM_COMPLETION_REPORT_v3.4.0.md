# Platform Completion Report — Lattice AI v3.4.0

**Goal:** complete the remaining core Local-First AI Workspace functionality the
v3.3.0 audit flagged — without touching Enterprise scope — and deliver a complete,
demonstrable platform with honest status throughout.

This report is evidence-first: each claim is backed by a code path and, where the
behaviour can be exercised, a runtime result.

---

## 1. Scope delivered

| # | Gap (per brief) | Prior state | v3.4.0 result | Verified |
| --- | --- | --- | --- | --- |
| 1 | Hooks Dispatch | registry only, no execution | execution engine + run log; fires from agents/workflows/tools/pipeline | Live server + 17 unit tests |
| 2 | Uploads ↔ Files | uploads invisible in Files | `/knowledge-graph/documents` + Files documents table | Ingest→list_documents smoke |
| 3 | VLM Image Input | backend-only | composer attach/drag/paste/preview + Vision badge | UI smoke + screenshot |
| 4 | Agent Run Trigger | only from Planning | Run console in Agents (Run/Stop/Status/Queue/Logs) | Live run + hooks fired |
| 5 | Desktop Local Agent | disabled | `/api/local-agent/status` (real on-device runtime) | Live endpoint |
| 6 | Connect Folder | disabled | `connectFolder()` over `/knowledge-graph/local/*` | Endpoint + UI |
| 7 | Folder Watch | disabled | `LocalKnowledgeWatcher` surfaced | Reindex-on-change verified |

Enterprise (SSO, SCIM, DLP, Private VPC, SIEM, enterprise RBAC/user-management)
was **not** implemented and remains honestly disabled.

---

## 2. Implementation by workstream

### Hooks Dispatch (`latticeai/core/hooks.py`, `api/hooks.py`, wiring)
- `HookContext` (mutable payload, `block()` gate, notes) and `HookResult`.
- `register_hook(id, runner)`, `run_hook`, `run_hooks(kind)`, `fire_hook`.
- In-process runners for built-ins (`redact-secrets`, `audit-agent-run`,
  `pipeline-index-status`) bound in `server_app.py`; user hooks execute their
  `command` as a subprocess with the context on stdin / `LATTICE_HOOK_CONTEXT`.
- `pre_*` gate semantics (block aborts the action; non-zero `pre_*` exit blocks).
- Persisted run log (`hooks_runs.json`) → `GET /api/hooks/runs`; `POST
  /api/hooks/run|fire` dispatch on demand.
- Fired from: `services/agent_runtime.py` (pre/post-run, block aborts the run),
  `core/workflow_engine.py` (start/end), `api/tools.py` `_tool_response`
  (pre/post-tool), `services/upload_service.py` (`document.ingested`).

### Uploads ↔ Files (`knowledge_graph.py`, `knowledge_graph_api.py`, `views/files.js`)
- `KnowledgeGraphStore.list_documents()` projects `Document` nodes with ingest +
  index state and chunk count → `GET /knowledge-graph/documents`.
- Files view: documents table via `api.documents()`, re-hydrated after upload;
  Connect-folder + connected-folders/watch panel.

### VLM Image Input (`api/models.py`, `api/chat.py` [existing], `views/chat.js`)
- `_vision_capability()` adds a `vision` block to `/models` from the active
  model's `model_compat.get_model_profile().supports_vision`.
- Chat composer: attach/drag/paste, preview+remove, sends `image_data`; Vision
  Enabled/Disabled badge.

### Agent Run Trigger (`views/agents.js`, existing `AgentRuntime`)
- Run console over `/agents/api/run` + `/agents/api/runtime/status` +
  `/agents/api/runs/{id}`; logs from `result.timeline`; honest Stop.

### Local Agent / Connect Folder / Folder Watch (`api/local_files.py`, `local_knowledge_api.py` [existing], `views/my-computer.js`)
- `GET /api/local-agent/status` reports real runtime/handshake/health/folders.
- `connectFolder()` self-approval flow; `LocalKnowledgeWatcher` debounced reindex.

### SPA client + components (`static/v3/js/core/api.js`, `components.js`)
- New adapter methods: `documents`, `localAgent`, `localRoots`, `localSources`,
  `localWatchStatus`, `localWatchStop`, `connectFolder`, `hookRun`, `hookRuns`.
- `STATE_VARIANT` extended with v3.4.0 status colors (ingested/watching/online/…).

---

## 3. Validation (run on the v3.4.0 code)

| Check | Command | Result |
| --- | --- | --- |
| Lint | `npm run lint` | **64/64** v3 modules pass |
| Typecheck | `npm run typecheck` | `tsc -p .` clean |
| Build | `npm run build` | assets (38) + Python build OK |
| Unit | `pytest tests/unit` | **388 passed** (17 new hooks tests) |
| Integration | `pytest tests/integration` (live server) | **9 passed** |
| Visual | `playwright test tests/visual/v3.spec.js` | **13 passed** |
| Runtime smoke | 5 views via Playwright | 0 console/page errors |

### Functional evidence (live server, `:8899`)
- `POST /api/hooks/run {hook_id: builtin:redact-secrets, payload:{token}}` →
  `status:ok, output:"redacted 1 field(s)"` (the runner really redacted the field).
- `POST /agents/api/run` → `status:ok`, roles `[planner,executor,reviewer]`,
  `pre_run_hooks.ran:1`, `post_run_hooks.ran:1` (no model loaded).
- `GET /api/hooks/runs` → 3 records, including the agent's auto-fired
  `Redact secrets (pre_run)` and `Audit agent run (post_run)`.
- `GET /api/local-agent/status` → `online:true`, handshake ok, honest
  `watcher_available` and 0 folders on a fresh instance.
- Folder Watch: creating files in a watched dir fired **2** debounced reindex
  events (with `watchdog` installed).

---

## 4. Public assets

Refreshed under `docs/assets/v3.4.0/`: `home`, `chat`, `vision-input`, `files`,
`connect-folder`, `knowledge-graph`, `memory`, `agents`, `agent-run`, `workflows`,
`settings`, `local-agent`, `hooks-dispatch`. Before/after for the changed views
under `docs/assets/v3.4.0/before/` (`files-before`, `chat-before`). All are real
captures of the built SPA against the visual mock harness
(`scripts/capture/capture_v340.js`). README screenshots and release-history table
updated to v3.4.0; stale v2.x/v3.2–3.3 hero/screenshots removed from the README.

---

## 5. Known limitations (honest)

- Live model output (VLM inference, agent-generated text) is **runtime-pending**:
  it needs a loaded local model and is not depicted in screenshots.
- Folder Watch requires `watchdog` (declared dependency); honestly degraded when
  absent.
- Standard-view headers render low-contrast at the top — pre-existing v3.3.1
  design (identical on unchanged views), not introduced here.
- Hosted/serverless deployment remains out of scope (local-first by design).

---

## 6. Release & registry policy

Build artifacts (npm tarball, PyPI sdist/wheel, VSIX) are produced for the release.
**Publishing to npm, PyPI, the VS Code Marketplace, and Open VSX is intentionally
NOT performed** — the project owner publishes manually. The branch, tag, GitHub
Release, and artifact results are recorded in the release itself and the final
hand-off report.
