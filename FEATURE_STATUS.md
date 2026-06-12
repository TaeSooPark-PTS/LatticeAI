# Lattice AI — Feature Status (v4.0.0)

**Release type:** Knowledge Graph First — the Knowledge Graph becomes the primary
architecture. Lattice AI is a Digital Brain Platform: the graph is the durable
asset; models read it and are replaceable. Every v3.6.0 claim below is backed by
automated tests.

## v4.0.0 Digital Brain Platform — what changed

v4 makes the v3.6.0 identity true in the implementation. Honesty ledger for
the transformation (every line cites code + tests; suite: **585 unit tests**):

| Area | Status | Evidence |
| --- | --- | --- |
| **Workflow execution** | WORKING (live) | Tool nodes EXECUTE via `dispatch_tool` under governance; approval-requiring tools pause runs (`awaiting_approval`) with a durable cursor; `WorkflowEngine.resume` re-enters without re-running completed nodes; denial fails honestly. The pre-v4 `{recorded:true}` runners are gone; skill nodes refuse explicitly. `test_t7_workflow_execution.py` (6). |
| **Multi-Agent Runtime** | WORKING (llm) / labeled simulation | `llm_role_runner` calls the loaded model (planner/executor/reviewer); unparseable output FAILS the run with raw preserved; `mode` persisted on every run record; simulations never write into the KG. `test_t7_llm_runner.py` (7), `test_truth_floor_t1.py`. |
| **Async run engine** | WORKING | Agent/workflow run endpoints persist queued rows, execute on server-loop tasks via `asyncio.to_thread`, stream progress through the realtime SSE feed, support cooperative cancellation, and mark orphaned active runs `interrupted` on startup while preserving approval pauses. `test_t7_async_run_executor.py` (4), `test_realtime.py`. |
| **Custom agents** | WORKING | Registry config (system_prompt/max_tokens/temperature) actually loaded at run time; honest skip in simulation. `test_t7_llm_runner.py`. |
| **Triggers** | WORKING | Interval scheduler (missed firings → recorded skips) + brain-event triggers on `kg_ingest.*`; `__trigger__` provenance on fired runs. `test_t7_triggers.py` (5). |
| **Ingestion coverage** | WORKING (4/5 paths) | Chat, MCP, uploads, browser/notes all route through `services/ingestion.py` with provenance; `GET /knowledge-graph/provenance/coverage` reports the honest ratio. Workspace OS events remain direct and are not claimed as graph provenance. `test_t4_ingestion_unification.py` (6). |
| **Durable conversations** | WORKING | `latticeai/brain/conversations.py` — unbounded SQLite in the KG db file (backup carries it); idempotent legacy import; the 50-message cap is dead. `test_t4_conversation_store.py` (7). |
| **Garden absorption** | WORKING | Vault = user-owned markdown mirror; brain authoritative (dual-write + startup import); chat garden context = brain query; `/garden/tree` fixed (was a latent 500). `test_t4_garden_absorption.py` (5). |
| **Memory & Context systems** | WORKING | Typed Decision/Experience nodes via the pipeline (simulations refused); `ContextAssembler` builds budgeted, provenance-traced chat context — workspace memories injected at inference for the first time; trace persists with the answer. `test_t5_memory_context.py` (10). |
| **Keyword search** | WORKING (FTS5/fallback) | Trigram FTS5 index w/ Korean substring recall; honest LIKE fallback + `fts_enabled` capability report. `test_kg_fts5.py` (7). |
| **Workspace scoping (reads)** | WORKING | Search channels + graph view filter by membership; `legacy` rows stay machine-visible (documented). `test_t6_scoped_reads.py` (5). |
| **By-id authorization** | FIXED | Snapshot get/area/export/compare + memory delete authorize against the record's own workspace; `/workspace/os` registry leak closed; chat context user isolation. `test_truth_floor_t1.py` (11). |
| **Auth hardening** | WORKING | Hashed session tokens at rest (transparent migration), 8+ alnum password policy, PKCE on SSO. `test_t6_auth_hardening.py` (6). |
| **Identity, policy, invitations, workspace state** | WORKING | Users migrate to stable UUIDs while sessions preserve email compatibility; workspace memberships and KG identity columns re-key non-destructively; `core/policy.py` backs admin enforcement and `/admin/roles`; local invitation tokens create/list/accept/expire; Workspace OS state imports into `knowledge_graph.sqlite`, mirrors JSON for compatibility, and no longer truncates durable collections. `test_t6_identity_policy_invitations.py` (4). |
| **Device identity + Brain Network v1** | WORKING | Ed25519 device keys; signed exports (tamper refused; unsigned-legacy local imports allowed); workspace-filtered export (header no longer lies); paired-peer push/receive with replay protection; `/network/*`; `/app#/network` surfaces device fingerprint, peer pairing, unpair, and signed push. `test_t8_brain_network.py` (7), `tests/visual/v3.spec.js`. |
| **Graph curation** | WORKING | `curate()` gated topic promotion + real `importance_score`; `POST /knowledge-graph/curate`. `test_t4_ingestion_unification.py`. |
| **Packaging** | FIXED | Wheel ships `setup_wizard.py` (root setup.py collision resolved); installed-wheel smoke test (`scripts/wheel_smoke.py`) in release CI; side-effect-free `create_app` factory (subprocess-verified). `test_app_factory.py`, `test_setup_wizard.py`. |
| **Privacy (frontend)** | WORKING | Zero CDN references in shipped pages (fonts/icons/chart.js/marked vendored); sw.js precaches the v3 bundle; mechanical lint gates (raw colors, inline styles, CDN). `test_t9_privacy_vendoring.py` (6). |
| **Graph explorer** | WORKING | Force-directed canvas (drag/zoom/pan/physics, token-colored) replaces the static SVG; Knowledge Graph is the landing view; brain-first navigation. lint:v3 all checks. |
| **v4 SPA parity + legacy retirement** | WORKING | Legacy static HTML/CSS/JS pages are deleted and compatibility GET routes redirect into `/app`; parity views cover token-native account/profile/password, workspaces/org members/invitations/activation, snapshots/time-machine/compare/export/merge-restore, activity/presence, run approvals/cancel/progress, workflow trigger config/status, Brain Network pairing/push, chat context trace, and KG provenance coverage. en/ko i18n is wired through `static/v3/js/core/i18n.js` and gated by `scripts/lint_v3.mjs`. `test_static_release_hygiene.py`, `test_workspace_os.py`, `tests/visual/v3.spec.js`. |
| **Honest numbers** | FIXED | Fabricated fusion meters removed; recall scores real (shared lexical scorer); recall graph branch fixed (`matches` key). |

### Known owner-only blockers (not implementation gaps)

| Gap | State today | Contract |
| --- | --- | --- |
| pptx history rewrite | deleted at HEAD only | owner decision (force-push) |
| Default production embedder | hash fallback, honestly reported | consent-gated wizard provisioning |

---

## v3.6.0 Knowledge Graph First — what's new

| Area | Status | Evidence |
| --- | --- | --- |
| **Unified ingestion pipeline** | WORKING | `latticeai/services/ingestion.py` — one entrypoint normalizes file/folder/web/tab/text into the graph, idempotent by content hash, routed through `dispatch_tool` (`pre_tool`/`post_tool` fire). `test_ingestion_pipeline.py` (8). |
| **Entity/relationship model** | WORKING | `kg_schema.py` +6 nodes (`Source`/`Repository`/`Meeting`/`Organization`/`Workflow`/`Agent`) +8 edges; additive, lossless `from_legacy`. `test_kg_schema_v36.py` (6). |
| **Browser/web ingestion** | WORKING (backend + extension scaffold) | `latticeai/api/browser.py` (`/api/browser/read-url`, `/ingest-current-tab`); MV3 extension under `browser-extension/` (127.0.0.1-only). `test_browser_ingestion.py` (10). Live URL fetch is exercised via an injected fetcher in tests; real fetch depends on network. |
| **Export/import/backup/restore** | WORKING | `latticeai/services/kg_portability.py` + `/api/knowledge-graph/{export,import,backup,restore,portability,provenance}`. Round-trip, dry-run, schema guard, backup→restore, integrity check. `test_kg_portability.py` (9). |
| **Provenance** | WORKING | `ingestion_provenance` table + `record/get/list/provenance_stats`; every node explainable. Covered by `test_ingestion_pipeline.py`. |
| **Hook coverage (ingestion)** | WORKING | KG ingestion now fires `pre_tool`/`post_tool` (closes the one v3.5.0 gap). `docs/RUNTIME_HOOK_COVERAGE_v3.6.0.md`, `test_runtime_coverage_v36.py`. |
| **KG-first UI** | WORKING (lint/build-verified) | Knowledge Graph view recast with Status/Sources/Capture/Backup tabs; `api.js` fallback-safe methods. Frontend gate: `lint:v3` 64/64 + asset build. Visual behavior not unit-tested (static frontend). |

Validation: unit suite green (incl. new v3.6.0 suites) · check:python · lint
64/64 · build (sdist+wheel+VSIX) · release artifact validation. Carry-over audit:
`docs/CARRYOVER_AUDIT_v3.6.0.md` (zero blocking items).

---

## v3.5.0 Stabilization — what hardened

| Area | Before v3.5.0 | v3.5.0 (verified) |
| --- | --- | --- |
| **Auth / OIDC** | SSO callback **base64-decoded** the `id_token` and trusted its claims — no signature/issuer/audience/expiry/nonce check (forgeable login) | Fail-closed verifier (`core/oidc.py`, RSA/JWKS): signature + `iss`/`aud`/`exp`/`nonce`; `alg:none`/`HS*` rejected; per-login nonce + state enforced (`test_oidc.py`) |
| **Proxy trust** | `client_ip` trusted `X-Forwarded-For`/`CF-Connecting-IP` unconditionally → per-IP rate limits spoofable | Forwarded headers honoured **only** from `LATTICEAI_TRUSTED_PROXIES`; else peer IP (`test_proxy_trust.py`, bypass proof) |
| **Runtime hooks** | `read_file`/`edit_file`/`grep`/`clear_history`, computer-use loop, skill-eval bypassed `pre_tool`/`post_tool` | All routed through `dispatch_tool`; 100% of discovered tool/agent paths covered (`docs/RUNTIME_HOOK_COVERAGE_v3.5.0.md`, `test_runtime_coverage.py`) |
| **`tools.py`** | one 1,525-line module | `tools/` package (computer/filesystem/documents/local_files/knowledge/network/commands + base + registry); flat imports preserved; no circular imports |
| **CI syntax gate** | hand-maintained `py_compile` list (still referenced deleted `tools.py`) | `scripts/check_python.py` discovers + compiles 144 modules; auto-includes new files |
| **UI surfaces** | command-palette scrim blur + 19 legacy `backdrop-filter: blur` surfaces | zero blur surfaces in active v3 CSS; solid/crisp; 13/13 visual tests pass |

Validation: lint 64/64 · check:python 144 · unit 419 · integration 9 · visual 13 ·
build (sdist+wheel, `tools/` included, twine PASSED).

---

## v3.4.1 Runtime Completion (prior release)

**Release type:** runtime completion — makes the v3.4.0 runtime systems
*verifiably* complete and corrects the v3.4.0 overclaims the implementation audit
found. Every v3.4.1 claim below is verified by a **live end-to-end run** against a
booted server (evidence: [`docs/assets/v3.4.1/e2e_runtime_log.txt`](docs/assets/v3.4.1/e2e_runtime_log.txt)),
not by unit tests, mocks, or endpoint existence alone.

## v3.4.1 Runtime Completion — what was partial in v3.4.0, now complete

The v3.4.0 audit found four runtime gaps; v3.4.1 closes them and the overclaims:

| Area | v3.4.0 reality (overclaim) | v3.4.1 (live-verified) |
| --- | --- | --- |
| **Hooks** | "fires from tools and workflows" — actually tool hooks fired from the **HTTP path only**; the agent + multi-agent + platform workflow paths bypassed hooks; 4/7 built-ins were advisory no-ops | One shared `dispatch_tool` lifecycle across **HTTP + agent + workflow** tool paths; workflow hooks fire from **both** the designer and platform paths; full `pre_/post_` × `run/tool/workflow/upload/index` lifecycle; **all 7 built-ins have real runners**; non-executable hooks are explicitly flagged `advisory` |
| **Local Agent** | `online`/`handshake`/`health`/`filesystem_access` were **hardcoded constants** | All **probed**: real filesystem write/read/delete, live graph reachability, derived `mode` (online/degraded/error), `pid`, `version`, handshake `latency_ms`, `last_seen`, `error` |
| **Connect Folder** | wired but **never run end-to-end** | Live: real folder → permission approval → index → Files table → retrieval → hybrid search |
| **Folder Watch** | verified only in isolation; `watchdog` absent at runtime | `watchdog` installed + declared; live create→reindex→`post_index` hook; **restore-on-restart** verified |

Live E2E result (booted server, isolated data dir): **7/7 PASS** + restore-on-restart PASS.
**Method:** every classification below is traced through source — UI view
(`static/v3/js/views/*.js`) → API adapter (`static/v3/js/core/api.js`) → FastAPI
router (`latticeai/api/*.py`) → service/core (`latticeai/services/*`,
`latticeai/core/*`, top-level `knowledge_graph.py`). No status is asserted
without a `file:line` citation. Where a path was verified by grep rather than a
full trace, that is stated.

**Frontend of record:** the v4 SPA at `/app` (`static/v3/`). The legacy static
pages have been removed; compatibility GET routes redirect into the matching
`/app#/...` surface.

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
**DISABLED**. v3.3.0 fixed the handful of real gaps found in the audit; **v3.4.0
closes the remaining functionality gaps it had flagged** — hooks now execute,
uploads appear in Files, the Chat composer accepts images, agents run from their
own view, and the on-device local agent / connect-folder / folder-watch surfaces
are live (see [CHANGELOG](CHANGELOG.md) and
[RELEASE_NOTES_v3.4.0.md](RELEASE_NOTES_v3.4.0.md)). Enterprise features remain
intentionally **DISABLED**.

---

## Summary table

| Area | Headline status | v3.4.0 change |
| --- | --- | --- |
| Chat | WORKING + **VLM image input now WORKING** | Composer image attach/drag/paste/preview + Vision badge; `image_data` → `/chat` |
| Models / Local Models | WORKING (local inference dep-gated) | `/models` now reports a `vision` capability block |
| Files / File Ingestion | WORKING — **uploads now appear in Files** | Documents table from `/knowledge-graph/documents`; Connect Folder enabled |
| Retrieval / Hybrid Search / Search | WORKING | — |
| Knowledge Graph | WORKING (config-dependent) | `list_documents()` + `/knowledge-graph/documents` |
| Memory | WORKING (recall = workspace+graph) | — |
| Agents | WORKING — **run trigger now in the Agents view** | Run/Stop/Status/Queue/Logs console; pre/post-run hooks fire |
| Workflows / Planning / Pipeline | WORKING (deterministic) | Workflow start/end hooks fire |
| Skills | WORKING (registry + filesystem) | — |
| Hooks | **WORKING — full lifecycle (v3.4.1)** | Shared `dispatch_tool` across HTTP+agent+workflow tool paths; both workflow paths; upload+index granularity; all 7 built-ins have real runners; `advisory` flag |
| MCP / Tools / Marketplace | WORKING management; live MCP calls PARTIAL | Tool hooks fire from **all** tool paths (v3.4.1), not just HTTP |
| Settings / Home / My Computer | WORKING — **Local Agent real probes (v3.4.1)** | `/api/local-agent/status` probed (fs/graph/mode/pid/handshake-latency); was hardcoded in v3.4.0 |
| Authentication | WORKING | — |
| Admin | WORKING read surfaces; Enterprise DISABLED | unchanged — Enterprise stays honestly disabled |

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

**WORKING in v3.4.0 (was PLACEHOLDER) — VLM image input.** The backend already
accepted `image_data` (base64) on `/chat`, decoded it and injected screenshot
context (`chat.py:187-210, 393-396`); v3.4.0 adds the composer affordance:
attach button + hidden file input, drag-and-drop, clipboard paste, a thumbnail
preview with remove, and `image_data` is sent on send (`chat.js`). A
**Vision Enabled / Disabled** badge reads the new `vision` block from `/models`
(`models.py` `_vision_capability`), which derives `supports_vision` from the
active model's compat profile (`model_compat.get_model_profile`). *Live VLM
inference output* still requires a loaded vision model (e.g. an MLX-VLM build) —
runtime-pending, honestly badged when absent.

**Notes / known minor gaps (not changed):** the side-panel retrieval context is
real when `/api/search/hybrid` and `/api/graph` respond (PARTIAL, honest empty
states otherwise); the `current URL` built-in command (`chat.py:328-341`) is
dead from v3 (adapter never sends `client_url`); several command/agent responses
are Korean-only while the SPA is English (i18n inconsistency).

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

**WORKING in v3.4.0 (was DISABLED) — Connect / watch a folder.** The "desktop
local agent" framing was misleading: the Lattice server *is* the on-device agent
(it runs locally with filesystem access). v3.4.0 surfaces the existing backend.
Files (and My Computer) now expose **Connect folder** → `api.connectFolder(path)`
which runs request → self-approve (the click is the consent) → index + watch
against `/knowledge-graph/local/index` (`local_knowledge_api.py:289-317`). A
Connected-folders panel lists sources with live **Folder Watch** state from
`/knowledge-graph/local/sources` and a Stop-watching action. The watcher
(`LocalKnowledgeWatcher`) genuinely fires debounced reindex on create/update/
delete (verified) when `watchdog` is installed — it is a declared dependency
(`requirements.txt`, `pyproject.toml`); when absent it honestly reports
`available:false` (`local_knowledge_api.py:75-91`).

**WORKING (API-only) — Indexing controls + local read/write/serve.**
`/workspace/indexing/{id}/pause|resume|remove` (`workspace.py:302-318`),
`/local/list|read|serve|write` with approval gating (`local_files.py:42-99`).

**FIXED in v3.4.0 — uploaded documents now appear in Files.** The v3.3.0
limitation (uploads created Document/Chunk nodes but the Files table only listed
`local_sources`, so uploads were searchable but invisible) is resolved.
`KnowledgeGraphStore.list_documents()` (`knowledge_graph.py`) surfaces every
`Document` node with its ingest + index state (`ingested` → `indexed` once
retrieval chunks exist), exposed at `GET /knowledge-graph/documents`
(`knowledge_graph_api.py`). The Files "Uploaded documents" table reads it via
`api.documents()` and **re-hydrates after every upload**, so a just-uploaded file
appears immediately — completing the upload → Files → Knowledge Graph → Hybrid
Search → Chat path. *Verified* end-to-end (ingest a doc → `list_documents` reports
it `indexed` with chunk count).

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

**WORKING in v3.4.0 (was PLACEHOLDER) — Run trigger from the Agents view.** The
Agents view now has a Run console: a goal field + role chips (seeded from
`runtime.default_pipeline`) → `api.runAgent(goal, roles)` (`POST /agents/api/run`)
with **Run / Stop / Status / Queue / Logs**. Runs are queued durably, then
completed by the async executor; logs poll the persisted row, the Queue tile
reflects `runtime.active_runs`, and Stop requests cooperative cancellation
(sync model/tool calls finish their current step before the final cancelled
status lands). *Verified* on a live server: a run completes (no model required)
and **fires pre_run + post_run hooks** (`ran:1` each) recorded in the hook run
log. No Planning-view dependency.

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

**WORKING in v3.4.0 (was PARTIAL/PLACEHOLDER) — hooks now execute.** The v3.3.0
honesty gap (registry-only, no dispatch site) is closed. `core/hooks.py` gains a
real execution engine: `HookContext` / `HookResult`, `register_hook(id, runner)`,
`run_hook`, `run_hooks(kind, …)`, and `fire_hook` (fire-and-forget). A hook runs
either via an **in-process runner** bound by its owning subsystem (built-ins —
`redact-secrets`, `audit-agent-run`, `pipeline-index-status` are bound at startup
in `server_app.py`) or, for user hooks, by executing their `command` as a
**subprocess** (context on stdin + `LATTICE_HOOK_CONTEXT` env). `pre_*` hooks
**gate**: a blocking `pre_run` aborts an agent run, a blocking `pre_tool` aborts
the tool call (a non-zero exit from a `pre_*` command hook blocks fail-closed).
Every dispatch is recorded to a bounded, persisted **run log** (`hooks_runs.json`)
exposed at `GET /api/hooks/runs`; `POST /api/hooks/run` fires on demand.

**v3.4.1 — full lifecycle coverage (corrects the v3.4.0 scope).** A single shared
`dispatch_tool` (`core/hooks.py`) drives `pre_tool → execute → post_tool` for
**all three** tool paths — the HTTP `/tools/*` routes
(`api/tools._tool_response`), the single-agent runtime (`core/agent.py` via
`AgentDeps.hooks`), and the workflow tool node (`platform_runtime._tool_node_runner`).
`pre_workflow`/`post_workflow` fire from **both** the designer endpoint and the
platform path (`platform_runtime.run_workflow_by_id` now passes `hooks` to
`WorkflowEngine`), so the multi-agent executor no longer bypasses workflow hooks.
The upload pipeline fires granular `pre_upload`/`post_upload` + `pre_index`/
`post_index`; the local-folder index and **folder-watch reindex** fire
`pre_index`/`post_index` too. **All 7 built-in hooks have real runners**
(`core/builtin_hooks.py`) — none is a silent no-op; a hook with no bound runner
and no command is flagged `advisory` in the registry + UI. Legacy `workflow`/
`pipeline` kinds are accepted and mapped forward. 19 unit tests
(`tests/unit/test_hooks_dispatch.py`). *Live-verified* (`e2e_runtime_log.txt`):
firing `builtin:redact-secrets` redacted a `token`; an HTTP tool call fired
pre_tool (real `sensitivity`/`policy` output) + post_tool; an agent run auto-fired
pre_run + post_run; an upload fired all four upload+index kinds.

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

**WORKING (v3.4.1 real probes; v3.4.0 was hardcoded) — Local Agent + Connect
Folder + Folder Watch.** My Computer's **Local Agent** panel reads
`GET /api/local-agent/status` (`local_files.py`). **v3.4.0 hardcoded**
`online`/`handshake`/`health`/`filesystem_access` to `true`; **v3.4.1 probes them
for real**: a filesystem write→read→delete in the data dir, a live
`knowledge_graph.stats()` reachability call, and a derived `mode`
(online/degraded/error) — plus `pid`, `version`, handshake `latency_ms`,
`last_seen`, and an `error` string when a probe fails. No fake readiness: a fresh
instance shows 0 folders and `watcher_available:false` when `watchdog` is absent.
The Connect-Folder + Folder-Watch panel mirrors the Files surface (connect, list,
stop-watch). *Live-verified* (`e2e_runtime_log.txt`): `mode=online`, real `pid`,
`handshake.latency_ms`, `graph_reachable=true`, `error=null`. The "Local Agent"
is honestly the on-device Lattice runtime — no separate desktop install.

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
