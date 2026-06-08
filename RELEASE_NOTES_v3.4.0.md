# Lattice AI v3.4.0 — Platform Completion

**Release type:** platform completion (functionality). Closes the remaining
non-enterprise gaps the v3.3.0 honesty audit flagged. Every change below is
runtime-verified on a live server, not only traced through source.

Lattice AI stays a **local-first** workspace: inference, indexing, files, and the
knowledge graph live on your machine. Enterprise features remain intentionally
disabled with honest "not available in this build" states.

---

## Highlights

### 1. Hooks now execute (was registry-only)

The v3.3.0 audit's most significant honesty gap is closed. `latticeai/core/hooks.py`
gains a real execution engine alongside the existing registry:

- `HookContext` / `HookResult`, and the public verbs `register_hook(id, runner)`,
  `run_hook`, `run_hooks(kind, …)`, and `fire_hook(kind, event, …)` (fire-and-forget).
- A hook runs either via an **in-process runner** bound by its owning subsystem
  (built-ins `redact-secrets`, `audit-agent-run`, `pipeline-index-status` are
  bound at startup) or, for user hooks, by executing their `command` as a
  **subprocess** (full context on stdin and in `LATTICE_HOOK_CONTEXT`).
- **`pre_*` hooks gate.** A blocking `pre_run` aborts an agent run; a blocking
  `pre_tool` aborts the tool call. A non-zero exit from a `pre_*` command hook
  blocks fail-closed; a misbehaving hook never crashes the lifecycle point.
- Every dispatch is appended to a bounded, persisted **run log**
  (`hooks_runs.json`), exposed at `GET /api/hooks/runs`. `POST /api/hooks/run`
  (and `/fire`) dispatch on demand.

Hooks fire from real lifecycle points: **Agents** (`AgentRuntime.start`
pre/post-run), **Workflows** (`WorkflowEngine.run` start/end), **Tools**
(`/tools/*` pre/post-tool), and the **upload Pipeline** (`document.ingested`).
The Hooks view adds a per-hook **Run**, a **Run by kind**, and a **Recent
executions** log so dispatch is observable.

### 2. Uploaded documents appear in Files

`KnowledgeGraphStore.list_documents()` + `GET /knowledge-graph/documents` surface
every ingested `Document` node with its ingest → index state (`ingested` →
`indexed` once retrieval chunks exist). The Files "Uploaded documents" table reads
it and **re-hydrates after every upload**, completing
upload → Files → Knowledge Graph → Hybrid Search → Chat.

### 3. VLM image input in Chat

The backend already accepted `image_data`; v3.4.0 adds the composer affordance —
**attach, drag-and-drop, paste**, a thumbnail **preview** with remove, and the
image is sent with the message. A **Vision Enabled / Disabled** badge reads a new
`vision` capability block from `/models` (derived from the active model's compat
profile). Live VLM inference output requires a loaded vision model and is honestly
badged when absent.

### 4. Run agents from the Agents view

A Run console (goal + role chips → **Run / Stop / Status / Queue / Logs**)
executes the multi-agent pipeline directly from Agents — no Planning-view detour.
The pipeline runs synchronously and **without a loaded model** (deterministic
runner), rendering the run's timeline as logs and firing pre/post-run hooks. Stop
honestly reports the synchronous runtime's `{stopped:false, reason}`.

### 5. On-device Local Agent + Connect Folder + Folder Watch

The "desktop local agent" is the Lattice server itself, running on your machine.
- `GET /api/local-agent/status` reports the **real** runtime state: online,
  platform/machine/python, an in-process handshake, filesystem-access and
  watcher-availability health, and connected/watching folder counts. No fake
  readiness — a fresh instance shows 0 folders and reports the watcher honestly.
- **Connect Folder** (`api.connectFolder`) runs request → self-approve (the click
  is the consent) → index + watch via the existing `/knowledge-graph/local/*`
  endpoints. The **Folder Watch** (`LocalKnowledgeWatcher`, `watchdog`) fires a
  debounced reindex on create/update/delete — verified.

---

## What did NOT change

Enterprise capabilities remain **disabled** and honestly labeled: SSO, SCIM, DLP,
Private VPC, SIEM, enterprise RBAC, and enterprise user management. The deterministic
agent runner is unchanged (documented as LLM-free). Local MLX inference still
requires Apple Silicon + an MLX/MLX-VLM build for local generation.

## Known limitations (honest)

- **Live model output is runtime-pending in screenshots.** VLM inference and
  agent-generated text require a loaded local model; the included screenshots show
  the real UI with representative data, not live inference.
- **Folder Watch requires `watchdog`** (a declared dependency). When absent, the
  Local Agent / Files surfaces honestly report `watcher_available:false`.
- **Standard-view headers render low-contrast at the top** — a pre-existing
  v3.3.1 design characteristic (visible identically on unchanged views), not
  introduced by v3.4.0.
- **Hosted deployment is out of scope.** Lattice AI is local-first (MLX,
  filesystem, SQLite) and is not a serverless/hosted product.

## Validation

- `npm run lint` — 64/64 v3 modules pass.
- `npm run typecheck` — `tsc -p .` clean (VS Code extension).
- `npm run build` — assets + Python build succeed.
- `pytest tests/unit` — 388 passed (incl. 17 new `test_hooks_dispatch.py`).
- `pytest tests/integration` — 9 passed against a live server.
- Playwright `tests/visual/v3.spec.js` — 13 passed.
- Runtime smoke — all five new/updated views render with zero console errors; the
  live server boots clean; hooks fire from manual + agent lifecycle; folder watch
  reindexes on change.

## Registry publishing

Build artifacts (npm tarball, PyPI sdist/wheel, VSIX) are produced for this
release. **Publishing to npm, PyPI, the VS Code Marketplace, and Open VSX is
performed manually by the project owner and is intentionally not done here.**
