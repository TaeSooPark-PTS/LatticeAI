# Lattice AI — Feature Status (v3.3.0)

**Release type:** product-quality / honesty audit (no new product areas).
**Method:** every classification below is traced through source — UI view
(`static/v3/js/views/*.js`) → API adapter (`static/v3/js/core/api.js`) → FastAPI
router (`latticeai/api/*.py`) → service/core (`latticeai/services/*`,
`latticeai/core/*`, top-level `knowledge_graph.py`). No status is asserted
without a `file:line` citation. Where a path was verified by grep rather than a
full trace, that is stated.

**Frontend of record:** the v3 SPA at `/app` (`static/v3/`). The legacy static
pages (`static/chat.html`, `admin.html`, `graph.html`, `workspace.html`, …) are
superseded and not the audited surface; some legacy routes still resolve (e.g.
`GET /agents` serves `static/agents.html`).

**Status legend**

| Status | Meaning |
| --- | --- |
| **WORKING** | End-to-end path exists in code and functions in a normal local run. |
| **PARTIAL** | Works for some cases / degraded or config-dependent / missing pieces. |
| **PLACEHOLDER** | UI renders but no real wiring, or backend exists with no UI caller. |
| **DISABLED** | Intentionally off with an honest "not available in this build" message. |
| **BROKEN** | Wired but errors / misbehaves. |

**Overall finding.** Lattice AI's `/app` is, before this release, already
unusually honest: the API adapter never fabricates data — it reports
`source: "live"` vs `"unavailable"` (`api.js:59-73`) and the chat fallback says
so in words (`api.js:393-408`). Most surfaces are **WORKING** or honestly
**DISABLED**. v3.3.0 fixes the handful of real gaps found here (see
[CHANGELOG](CHANGELOG.md)).

---

## Summary table

| Area | Headline status | v3.3.0 change |
| --- | --- | --- |
| Chat | WORKING (doc-gen was BROKEN) | Fixed doc-gen SSE; honest grounding-chip copy |
| Models / Local Models | WORKING (local inference dep-gated) | — |
| Files / File Ingestion | WORKING upload (was PLACEHOLDER UI) | Drop zone now uploads to `/upload/document` |
| Retrieval / Hybrid Search / Search | WORKING | — |
| Knowledge Graph | WORKING (config-dependent) | — |
| Memory | WORKING (recall = workspace+graph) | Honest header/recall copy |
| Agents | WORKING (deterministic runner) | — |
| Workflows / Planning / Pipeline | WORKING (deterministic) | — |
| Skills | WORKING (registry + filesystem) | — |
| Hooks | PARTIAL — management works, **execution is PLACEHOLDER** | Documented honestly |
| MCP / Tools / Marketplace | WORKING management; live MCP calls PARTIAL | — |
| Settings / Home / My Computer | WORKING | Version from `/health`; Home retrieval status fixed |
| Authentication | WORKING | — |
| Admin | WORKING read surfaces; Enterprise DISABLED | — |

---

## Chat

**WORKING — Send → model → streamed answer.** `chat.js:266 api.streamChat()` →
`api.js` POST `/chat` SSE → `latticeai/api/chat.py:252` → `_stream_chat`
(`chat.py:593-630`) yields `{chunk, model}` then trace + `[DONE]` →
`llm_router.stream_generate` (`llm_router.py:566-627`) for MLX-local or
OpenAI-compatible cloud. *Deps:* a loaded model; MLX (`mlx_vlm`) for local or
`OPENAI_API_KEY` for cloud. *Action:* none.

**WORKING — No-model-loaded handling.** `chat.py:343-355` returns 400
`{error:"no_model_loaded", action:"load_model"}`; adapter preserves it
(`api.js:284-291`) and the view shows an actionable banner, not a fake reply
(`chat.js:287-321`). Covered by `tests/unit/test_v3_chat_no_model.py`.

**WORKING — "Chat unavailable" transport fallback.** `simulateChat`
(`api.js:393-408`) emits an explicit "backend or active model is not reachable"
message tagged `source:"unavailable"`; fired only on real failures.

**WORKING — Conversation history list/open/delete.** `/history/conversations*`
(`chat.py:531-560`), persisted each turn (`chat.py:418, 615`).

**WORKING — Model pill / current model.** `chat.js:410-416` from `GET /models`.

**FIXED in v3.3.0 (was BROKEN) — Document-generation streaming.** The doc-gen
branch (`chat.py:422-464`) streams SSE keyed `text`, but the v3 parser only read
`data.chunk` (old `api.js:313`) → report requests rendered a *false* "Couldn't
reach the model" while the backend actually generated and saved the document
(`chat.py:448`). **Fix:** the parser now accumulates `data.chunk || data.text`
(`api.js`). *Action done.*

**PARTIAL → honest copy (grounding toggles).** The Knowledge Graph / Vector
chips set `state.grounding` (`chat.js:53-57`) and are sent on `/chat`, but
`ChatRequest` (`chat.py:32-44`) has no `grounding` field, so pydantic drops it —
the model always receives KG + gardener context (`chat.py:365-391`). The chips
*do* drive the retrieval-context preview's mode. **v3.3.0:** relabeled the chip
tooltip to "Show the … signal in the retrieval-context panel" so it no longer
implies it gates generation. *Future option:* add `grounding` to `ChatRequest`
and honor it.

**Notes / known minor gaps (not changed):** the side-panel retrieval context is
real when `/api/search/hybrid` and `/api/graph` respond (PARTIAL, honest empty
states otherwise); the `current URL` built-in command (`chat.py:328-341`) is
dead from v3 (adapter never sends `client_url`); several command/agent responses
are Korean-only while the SPA is English (i18n inconsistency); VLM image input is
accepted by the backend but has no v3 composer affordance.

---

## Models / Local Models

**WORKING — Model list + load/unload/switch + recommendations.** `GET /models`
(`models.py:255`), `/models/load` (`:274`), `/models/switch` (`:293`),
`/models/unload[-all]` (`:302/:308`), `/models/recommendations` (`:315`);
engine management `/engines/install|verify-cloud|pull-model|prepare-model[/stream]`
(`models.py:136-204`), `/setup/set-api-key` (`:232`). The adapter distinguishes
loaded vs available honestly (`api.js models()` 148-170). Backed by
`services/model_catalog.py`, `model_runtime.py`, `model_recommendation.py`.

**PARTIAL — Local MLX inference actually runs.** Requires Apple Silicon +
optional `mlx-vlm` (`pyproject.toml` `[local]` extra). Without it, local
generation is unavailable and chat reports `no_model_loaded` — honest, not fake.
Cloud (OpenAI-compatible) works with a key. *Action:* none; document the MLX
dependency in release notes.

**WORKING — Embeddings provider status.** `GET /api/embeddings/status`
(`api.js:189-202`) surfaces the active provider/grade/dims; the default
`LocalEmbeddingModel` is a deterministic feature-hashing embedder honestly
labeled "fallback" (`local_embeddings.py:48`).

---

## Files / File Ingestion

**FIXED in v3.3.0 (was PLACEHOLDER) — Manual document upload.** A complete
backend already existed — `POST /upload/document` (`tools.py:421-434`) →
`process_uploaded_document` (`upload_service.py:15-96`): extension whitelist
(`.pdf/.docx/.xlsx/.pptx/.txt/.md/.csv`), 10 MB cap, magic-byte check, parse,
**chunk → embed → knowledge-graph ingest** (`knowledge_graph.ingest_document`),
audit log. But **no v3 view called it**; the Files drop zone had zero handlers
(decorative). **Fix:** `files.js` drop zone + header/empty-state buttons now do a
real drag-and-drop / picker upload via new `api.uploadDocument()` (multipart),
with progress + result toasts and a table refresh. *Action done.*

**WORKING — Indexed-sources table.** `files.js:116 GET /workspace/indexing`
(`workspace.py:295`) → `WORKSPACE_OS.build_indexing_dashboard` reads real KG
stats + `graph.local_sources()` (`knowledge_graph.py:1709-1743`).

**DISABLED (honest) — Connect / watch a folder.** All connect entry points show
"Connecting a folder requires the Lattice desktop local agent — not available in
this build." (`files.js`). Backend folder-indexing (`/knowledge-graph/local/index`,
`LocalKnowledgeWatcher`) exists but is intentionally not surfaced; the watcher
honestly reports `available:false` when `watchdog` is absent
(`local_knowledge_api.py:75-91`).

**WORKING (API-only) — Indexing controls + local read/write/serve.**
`/workspace/indexing/{id}/pause|resume|remove` (`workspace.py:302-318`),
`/local/list|read|serve|write` with approval gating (`local_files.py:42-99`).

**Known limitation (documented, not fixed):** uploaded documents create
Document/Chunk/embedding nodes but **not** a `knowledge_sources` row, while the
Files table lists only `local_sources`. So an uploaded file is searchable in
Chat/Hybrid Search but does **not** appear in the Files table. The upload success
toast states this ("now searchable in Chat and Hybrid Search") to set
expectations. *Future option:* record a source row on upload, or add a
"Documents" tab fed by Document nodes.

---

## Retrieval / Hybrid Search / Search

**WORKING — Hybrid (fused) search.** `hybrid-search.js:97 api.hybridSearch()` →
`POST /api/search/hybrid` (`search.py:65-78`) → `search_service.hybrid_search`
(`search_service.py:162-226`) genuinely runs keyword + vector + graph channels
and fuses with weighted `max(score, 1/rank)`, returning real per-signal
`source_scores`. No canned results anywhere in the path.

**WORKING — Vector / Keyword / Graph channels.** Vector cosine over
`vector_embeddings` (`knowledge_graph.py:3728-3797`); keyword SQLite LIKE +
ranking (`:3166-3225`); graph proximity + relationship expansion (`:3349/3424`,
`search_service.py:86-160`).

**WORKING — Index status + rebuild.** `/api/index/status` (`:186`) and
`/api/index/rebuild` (`:193`) back real `index_status`/`rebuild_vector_index`
(`knowledge_graph.py:3653/3543`).

**WORKING — Honest unavailable state when KG off.** Service raises → 404 →
adapter `source:"unavailable"` → view shows empty state, no fabricated results.

**PLACEHOLDER (cosmetic) — "How fusion scores a match" explainer meters.**
`hybrid-search.js:183` renders hardcoded illustrative bars (0.85/0.7/0.55) in the
pre-query intro. *Action:* label as illustrative (low priority).

---

## Knowledge Graph

**WORKING (config-dependent) — Graph view + stats.** `knowledge-graph.js` →
`api.graph()` (`/api/graph` then legacy `/knowledge-graph/graph`) and
`graphStats()` (`/knowledge-graph/stats`), backed by the real SQLite KG
(`knowledge_graph.py`, ~177 KB: nodes/edges/traverse/relationship search/vector
ops all implemented; `knowledge_graph_api.py`). Renders real extracted
entities/relations when `ENABLE_GRAPH` and data exist; honest unavailable empty
state otherwise. *Deps:* `LATTICEAI_ENABLE_GRAPH` (default true).

---

## Memory

**WORKING — Memory Manager dashboard.** `memory.js:56 api.memoryManager()` →
`memory.py:48` → `memory_service.manager` (`memory_service.py:123-190`) builds six
tiers from real stores (workspace/project from WorkspaceOS, agent from snapshots,
conversation from `chat_history.json`, graph/vector from KG). Honest
"unavailable" health when a tier has no backing.

**WORKING — Workspace/agent/conversation tiers; unified recall; compact.** Recall
(`memory_service.py:196-238`) merges workspace `search_memories` + KG `search`.
Compact dedupes and persists (`:277-294`).

**PARTIAL — Project / graph / vector tiers.** Real but config/scenario dependent
(org workspaces; `ENABLE_GRAPH`); some `size_bytes` are hardcoded `0` (shown as
"—", not faked).

**PARTIAL (API-only) — Prune / Clear.** Backend + adapter exist
(`memory.py:74-107`, `api.js`) but there is no UI control. **v3.3.0:** corrected
the view header + recall copy that overstated this (recall searches workspace +
graph, not all six tiers). *Action done (copy).* 

---

## Agents

**WORKING — Roster + runtime status.** `agents.js:57 api.agentRuntime()` →
`/agents/api/runtime/status` (`agents.py:60`) → `agent_runtime.py:138-158` from
real persisted `agent_runs`.

**WORKING — Multi-agent pipeline execution.** `POST /agents/api/run`
(`agents.py:161`) → `MultiAgentOrchestrator.run` (`multi_agent.py:460-561`) drives
planner→executor→reviewer with real handoffs, review, retry loop, and persisted
replayable runs. **The default runner is deterministic and LLM-free by design**
(`multi_agent.py:6-8`) — it reports "Completed N/M planned step(s)", it does not
call a model. Document this in release notes.

**WORKING — Agent Registry.** list/capabilities/register/enable-disable/remove
(`agent_registry.py` API + core), persisted to `agent_registry.json`; builtin
removal honestly blocked.

**PLACEHOLDER — Run trigger from the Agents view.** `agents.js` never calls
`runAgent`; execution is reachable from **Planning**. The Agents page is
display + registry only. *Action:* add a Run affordance or adjust copy.

**Design note (not a bug):** registry enable/disable is metadata — execution
always runs `CORE_PIPELINE`; custom registered agents have no runner, so they are
not executable. The roster `state` and `runtime.ready` are constants
(`agent_runtime.py:130/147`).

---

## Workflows / Planning / Pipeline

**WORKING — Workflow definitions CRUD + run + replay.**
`/workflows/api/definitions*`, `/validate`, `/{id}/run`, `/runs`,
`/runs/{id}/replay` (`workflow_designer.py:71-201`) backed by
`core/workflow_engine.py`. Adapter methods in `api.js` (`workflowDefinitions`,
`runWorkflow`, …).

**WORKING — Planning.** `planning.js:56 api.runAgent()` executes the multi-agent
pipeline (same deterministic runner as Agents).

**PARTIAL — Pipeline view.** Renders ingest/embed/graph stages from real index
status; it visualizes flow rather than triggering arbitrary jobs.

---

## Skills

**WORKING — Skill registry + enable/disable/install/uninstall/update.**
`/workspace/skills*` (`workspace.py:522-562`) and marketplace `/skills/*`
(`mcp.py:257-305`), backed by `core/plugins.py` + on-disk `skills/` (real
`SKILL.md` directories). Execution occurs via the tool/agent runtime. *Action:*
none.

---

## Hooks

**PARTIAL — management WORKING, execution PLACEHOLDER.** `HooksRegistry`
(`core/hooks.py:116`) supports list/get/inspect/enable/reorder/register/remove,
persisted, and the API (`/api/hooks*`, `hooks.py:46-101`) + view are fully
wired — you can register and order hooks. **However, no runtime dispatch site
exists:** a grep for `run_hooks` / `fire_hook` / `dispatch_hook` / `emit_hook` /
`trigger_hooks` across the codebase returns nothing. Registered hooks are
*inspectable* (which the module docstring claims, `core/hooks.py:6`) but are **not
actually fired** during runs/tools/workflows. *User impact:* a user can configure
hooks that never execute — the most significant remaining honesty gap.
*Recommended action (next release, not v3.3.0):* either implement hook dispatch
at the documented lifecycle points, or relabel the Hooks view as a
"registry / preview" of where hooks *would* run. Flagged here rather than
silently fixed to avoid scope expansion.

---

## MCP / Tools / Marketplace

**WORKING — MCP management.** `/mcp/tools|installed|custom|connectors|claude-code-servers`,
`/mcp/registry/refresh`, `/mcp/recommend`, `/mcp/install` (`mcp.py:106-243`)
backed by `mcp_registry.py` (~41 KB) + `core/tool_registry.py`.

**PARTIAL — Live MCP tool calls.** `POST /mcp/call` (`mcp.py:354`) exists;
whether a call succeeds depends on actual connected/authenticated servers in the
environment. Honest by construction (failures surface as errors, not fake
success).

**WORKING — Tool registry + governance.** `/tools/permissions` and tool dispatch
(`api/tools.py`, `core/tool_registry.py`, `services/tool_dispatch.py`).

**WORKING — Marketplace templates.** five named agent templates +
clone/export/import/install over the local catalog (`core/marketplace.py`;
covered by `tests/unit/test_v32_platform.py`).

---

## Settings / Home / My Computer

**WORKING — Settings.** Theme/mode persist immediately (`settings.js`);
embeddings status is live; integration-readiness probes report live/unavailable
per endpoint. **v3.3.0:** the About panel now reads the version from `/health`
(was hardcoded `v3.1.0`) — single source of truth, no frontend version literal.

**FIXED in v3.3.0 — Home retrieval status.** `/api/index/status` is vector-centric
and emits no `pipelines` key, but `components.js` pillars/`indexChip`
(`:167/:200`) read `pipelines.{knowledge_graph.entities, vector_index.vectors,
hybrid.strategy, *.state}` → Home always showed a false "Retrieval status
unavailable". **Fix:** `api.indexStatus()` now synthesizes the `pipelines` shape
from the real index status (vectors) + KG stats endpoint (entities), staying
honest (unavailable stays unavailable; a missing entity count yields an
"unavailable" graph pillar, never a fabricated number).

**WORKING / PARTIAL — My Computer.** `/local/sysinfo` and
`/workspace/computer-memory` (`api.js sysinfo/computerMemory`). Hardware stats are
real where the host exposes them; consent-gated computer memory.

---

## Authentication

**WORKING — Session auth + RBAC + public mode.** `REQUIRE_AUTH` from config
(`server_app.py:172`); `require_user` raises when unauthenticated
(`server_app.py:764-766`); `require_admin` (`:837`) and a fixed RBAC model
(owner · admin · member · viewer, surfaced in `admin-permissions.js`). Public mode
(`IS_PUBLIC_MODE`, `server_app.py:160/962`) serves a public model and is honestly
labeled. Auth router `auth.py` + sessions `core/sessions.py`. *Action:* none.

---

## Admin

**WORKING — Read surfaces.** `/admin/summary|stats|users|audit|roles|policies`,
`/vpc/status`, `/admin/sso`, `/admin/enterprise` (`admin.py:61-243`) return real
local data (users, audit trail, roles) via `core/audit.py` + WorkspaceOS.

**DISABLED (honest) — Enterprise governance & mutations.** The admin views show
"not available in this build" for DLP rule editing (`admin-security.js:41`), SIEM
/ audit export (`admin-audit.js:44`), Private VPC (`admin-private-vpc.js:9`),
user management (`admin-users.js:11`), and permission editing
(`admin-permissions.js:176`). The backend `core/enterprise.py` /
`enterprise_admin.py` report every Enterprise capability `enabled=False` with a
`COMMUNITY_NOTICE` ("Enterprise extension point and is not [available]") and
**never gate any Community feature**. `siem_export_stub` returns the envelope
*shape* only. This is the honesty pattern the release targets. *Action:* none.

---

## Cross-cutting honesty mechanisms (verified)

- **No fabricated data.** `api.js withFallback` (`:67-73`) returns empty data +
  `source:"unavailable"` on any failure; `unavailableData` never invents
  counters. Every view renders a source badge.
- **No fake chat answers.** `no_model_loaded` is preserved; `simulateChat` is
  explicitly labeled unavailable.
- **Honest disabled states.** Folder connect, admin Enterprise features, and DLP
  all carry consistent "not available in this build" copy.
- **Deterministic agent runner** is documented as LLM-free, not hidden.

## Deployment readiness (evidence)

The Vercel project `lattice-ai` (FastAPI framework) has a single production
deployment that is **READY in build state but returns HTTP 500 on every route**
(`could not import "server.py"`, runtime logs). `project.live = false`. Lattice
AI is a **local-first** app requiring local MLX/filesystem/SQLite; it is **not
production-ready on Vercel serverless** and should not be presented as a hosted
product. See [STYLE_SYSTEM.md → Vercel MCP findings](STYLE_SYSTEM.md).
