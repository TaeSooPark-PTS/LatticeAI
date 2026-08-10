# Feature Audit — v11.2.0

> **Status: reference** — a point-in-time, evidence-based sweep of every row in
> [`FEATURE_STATUS.md`](../FEATURE_STATUS.md). It records what was true when it
> was run, not what the current release claims; the canonical feature state
> stays in `FEATURE_STATUS.md`.

Audited against the `release/v11.2.0` working tree on 2026-08-10, starting from
the 11.1.0 feature table. Continues the evidence-based audit tradition started
in 3.3.0: **every row gets a verdict and a way to reproduce it**, and a row
whose sentence outruns its code is lowered rather than defended.

## Method

Claims were checked by running the product, not by reading about it.

* **Hermetic full-app boot.** `latticeai.app_factory.build_runtime()` inside
  `scripts/export_openapi.py`'s `isolated_runtime_environment`, which points
  `HOME`, `LATTICEAI_DATA_DIR`, `LATTICEAI_BRAIN_DIR`, the agent workspace, the
  vault dir and the static dir at a throwaway temp tree. No real HOME, no real
  network, no live server. Endpoints were exercised through `TestClient`.
* **Three passes.** (1) anonymous read surface, (2) corrected paths with
  redirect-following off plus API introspection, (3) a registered-and-signed-in
  session driving the local-file approval dance end to end.
* **Route inventory** from the app's own route table and its generated OpenAPI
  schema, cross-checked against the checked-in `frontend/openapi.json` that
  `scripts/check_openapi_drift.mjs` already proves is regenerated from the live
  app.
* **Code-level verification** for claims an endpoint cannot show (constants,
  defaults, guard order, which collaborator writes which key).

Where a claim is pinned by an existing regression test, that test is the
evidence: it is cheaper to keep true than a paragraph.

## Cross-cutting smoke

Measured on the hermetic boot described above.

| Measure | Value |
| --- | --- |
| Routers registered (`include_router` calls, all distinct) | 35 |
| Total routes on the app | 451 |
| Of those, API routes | 446 |
| Non-API routes | 4 (`/openapi.json`, `/docs`, `/docs/oauth2-redirect`, `/redoc`) |
| Static mounts | `/static` always; `/icons` only when `static/icons/` exists (it does in the repo; it did not in the sandboxed static dir) |
| OpenAPI paths | 404 |
| OpenAPI operations | 445 |
| `frontend/openapi.json` drift | clean (`node scripts/check_openapi_drift.mjs`) |
| Health snapshot | `status: ok`, `version 11.1.0`, `mode local`, `graph_enabled true`, `telegram_enabled false`, `autoload_models false` |
| Probes run | 70 (pass 1) + 42 (pass 2) + the signed-in pass; **zero 5xx, zero unhandled exceptions** |

Every non-200 in the sweep was a *designed* refusal, and each named its
reason: `403` on the share routes while `LATTICEAI_BRAIN_NETWORK` is off,
`401` on local-file routes without a session, `405` on a POST-only path,
`422` on a malformed body.

### Frontend ↔ real routes

320 server-path literals were extracted from `frontend/src`. **Every one
resolves to a real route.** Ten looked missing on a first pass and all ten are
template routes the client builds with an interpolated id (`/models/switch` →
`/models/switch/{model_id:path}`, `/mcp/connectors` → `/mcp/connectors/{mcp_id}`,
and so on); the eleventh, `/workspace-admin`, is a client-side hash route, not a
server path.

### mock_server ↔ real routes

`tests/visual/mock_server.cjs` serves the twelve release-capture screens.

| Measure | Value |
| --- | --- |
| Exact path literals in the mock | 132 |
| Matching a real route (exact or template) | 131 |
| Mock-only | **1** — `/v3`, a legacy alias for `/app` that the server no longer serves |
| Prefix handlers in the mock | 17, all resolving to real routes or mounts |
| Real routes the frontend can call that the mock does not serve | 183 |

The 183 are capture-fidelity headroom, not product defects: the mock answers
what the twelve screens need and falls through for the rest. The gap is bounded
by a real gate — `tests/visual/v3.spec.js` asserts
`service-unavailable-banner` count is zero across all ten plain-mode routes, so
a mock gap that reaches a core panel fails the visual suite rather than shipping
a screenshot of a working feature rendered as broken.

## Verdict table

`WORKS` — the claim was exercised and held. `PARTIAL` — the mechanism exists
and is tested, but a surface the sentence implies does not reach it.
`MISMATCH` — the doc said something the code does not do.

| # | Row | Verdict | Evidence |
| --- | --- | --- | --- |
| 1 | Brain Home | **MISMATCH → doc fixed** | Living Brain + composer in-viewport asserted at 1280×800 and 390×780 (`tests/visual/v3.spec.js`); the Brain Brief renders only inside `BrainHomeDock`'s `stats` tab, and `BrainHome.test.tsx` asserts the dock opens closed |
| 2 | Automation Intelligence | WORKS | `GET /api/automation/{overview,patterns,suggestions}` → 200 with `consent.default_state: "draft_disabled"`, `requires_user_enable: true`, `review_before_run: true` |
| 3 | Brain Intelligence | **PARTIAL → code fixed** | `GET /api/brain/health` → 200 with all four dimensions; on an empty Brain three read `unavailable` yet the composite reported `overall_score: 100, grade: "excellent"` (see Finding 3) |
| 4 | Temporal Knowledge | WORKS | `KnowledgeGraphStore.as_of(timestamp, …)` and `neighbors(…, as_of=None)` present with the documented defaults; `tests/unit/test_t2_temporal_schema.py`, `test_t2_temporal_reads.py`, `test_kg_temporal.py` |
| 5 | Proactive Synthesis | WORKS | `POST /api/brain/synthesize` → 200 with the four proposal kinds; `SYNTHESIS_THRESHOLD_ENV` / `DEFAULT_SYNTHESIS_THRESHOLD = 25`; `tests/unit/test_t2_synthesis_job.py` |
| 6 | Memory Decay | WORKS | `GET /api/brain/importance` → 200 (`half_life_days: 30.0`, `access_source: "metadata"`); `/api/brain/quality-report` carries `importance` + `tidying`; `tests/unit/test_t2_importance_decay.py` |
| 7 | Hybrid Recall | WORKS | `POST /api/memory/recall` → 200 with `quality_gate.gate == "hybrid-evidence/v2"`; `GET /api/search/hybrid` reports per-channel weights and recall detail |
| 8 | Folder Ingestion | WORKS | `GET /api/ingestion/jobs` → 200; `/api/ingestion/folder`, `/jobs/{id}`, `/jobs/{id}/resume` present |
| 9 | Extraction Quality | WORKS | `assess_extraction_quality` + observe-mode `_observe_quality_gate` (every failure path returns `None` and the ingest proceeds); `tests/unit/test_ingestion_quality_jobs.py` |
| 10 | Vector Freshness | **PARTIAL → code fixed** | `GET /api/brain/vector-freshness` returned only `{status, pending_items, total_items, detail}` while `vector_freshness_breakdown()` had **no product caller** (see Finding 4) |
| 11 | Vector Index Backends | WORKS | `resolve_vector_index({})` → `brute`/exact; an unknown name falls back **and says why** in `detail`; choices are exactly `brute`/`quantized`/`hnsw` |
| 12 | Background Embedding | WORKS | `IngestionPipeline.drain_vector_queue` present; the Known Limitation that nothing schedules it is accurate |
| 13 | Fusion Strategy | WORKS (off) | `LATTICEAI_FUSION_STRATEGY` unset in a clean boot; alpha fusion is what `/api/search/hybrid` reports |
| 14 | Graph Candidate Expansion | WORKS (off) | `graph_expansion_enabled()` false with no env; `GRAPH_EXPANSION_ENV` as documented |
| 15 | Self-Model (Personal Ontology) | WORKS | `POST /api/memory/self-model/propose` with `저는 …를 선호합니다` returned 1 candidate and **one pending review item**, nothing written; `apply` calls `review_queue.approve()` before `_write_fact`; summary capped at half the context budget |
| 16 | Workspace Reorganization | WORKS | one `folder_reorganization` proposal, targets always `topics/<주제>/`, `unplaced` reasons, **no delete path exists in the module**, skip-and-report on apply |
| 17 | Change Governance | WORKS | `MUTATING_TOOL_INVENTORY` + `assert_governance_coverage`; base-SHA conflict → 409; `_atomic_write` via `os.replace` under a lock; verifier fails closed to `NEEDS_REVIEW` |
| 18 | Brain Brief | WORKS | `/api/memory/brain-brief` and `/api/brain/proactive-brief` → 200; the proactive brief counts existing proposals and raises none |
| 19 | Conversation | WORKS | `tests/unit/test_chat_no_model.py`; the loaded-model list is empty in a clean boot and chat refuses rather than inventing output |
| 20 | Knowledge Graph | WORKS | `/api/graph`, `/knowledge-graph/stats`, `/api/knowledge-graph/provenance` → 200; v2 schema reported alongside v1 |
| 21 | Source Capture | WORKS | unified pipeline reachable from files/folders/notes/web; provenance keyed by origin (no wall-clock component) |
| 22 | Local Models | WORKS | `GET /models`, `/models/recommendations` → 200 with a real device profile; `allow_model_downloads: false` in a clean boot |
| 23 | Installer Audit | WORKS | `auto_setup` refuses to apply on a confirmation-token mismatch (`CommandConfirmationError`); `tests/unit/test_setup_wizard.py`, `test_cov_wp04_wizard_install.py` |
| 24 | Cloud Models | WORKS | `_load_cloud_model` raises `Missing API key env var: …` **before** the client is constructed; egress is audited before any call |
| 25 | Telegram | WORKS | `run_bot` returns without polling on any of: no token, empty allowlist, no server session; ACL check precedes `register_chat_id` on both message and callback paths |
| 26 | Agent Runtime | WORKS | `/agents/api/runtime/health` → `status: "unavailable"` with the honest reason ("No LLM-backed model is loaded; product execution API refuses simulation runs"); preview records no run; unknown roles rejected |
| 27 | Tool Registry / MCP | WORKS | `/tools/registry/diagnostics` → `registered_tools 47 / governed_tools 47`, empty `governance_without_handler` and `handler_without_governance`; `/mcp/installed`, `/mcp/tools` → 200 |
| 28 | Workspaces | WORKS | `/workspace/os` → 200 with the personal workspace default; admin surfaces on separate routes |
| 29 | VS Code Extension | WORKS | `/workspace/vscode/status` → 200 (`connected:false, status:"offline"`); `POST /agent` `stream:true` dispatches `agent_step`; 4xx never reads as success |
| 30 | Browser Extension | WORKS | every outbound call is `http://127.0.0.1:${port}`; an absent verdict renders `근거 확인 불가`; no approve/reject call exists in the extension |
| 31 | Telegram Review | WORKS | `/review` reads the same `/api/proposals`; 409 → "아무것도 쓰지 않았습니다", no retry |
| 32 | Knowledge Garden | WORKS | `GET /api/brain/garden` → 200 with exactly four beds; an unavailable graph yields empty beds |
| 33 | Agent File Tasks | WORKS | `executor_prompt_for(base, *, profile, self_model_summary)`; `EXECUTOR_PROMPT` itself carries no file-task hints; summary injected only when passed |
| 34 | Agent Profiles | WORKS | `COMPACT_MAX_PARAMS_B = 4.0`; an id naming no size keeps `standard`; a failed direct-path write is reported as not written |
| 35 | Folder Memory State | WORKS | `GET /knowledge-graph/local/health` → 200; vector freshness reported once and labelled `vector_freshness_global` |
| 36 | Voice Capture | WORKS | `GET /api/capture/voice/status` → `capture:true, transcription:false` with a plain-language reason; the transcriber really is one shared port (`ctx.MULTIMODAL_PORTS.transcriber` feeds both the pipeline and `VoiceCaptureService`) |
| 37 | Multi-modal Memories | WORKS | `ALLOW_MULTIMODAL_ENV` default `"0"`; both modes asserted in `tests/unit/test_t3_ingest_routing.py`; `NodeType.AUDIO` and `IMAGE` are first-class |
| 38 | Vision OCR / Caption / Embedding | WORKS | `VisionStub` is gone from product code and from the shipped wheel/sdist, with a test asserting its absence; `resolve_vision_embedder` has no hash fallback |
| 39 | Image Retrieval | **PARTIAL → code fixed** | late fusion, the separate `image_embeddings` table, width-mismatch skipping and the `multimodal` result block are all real and tested — but no production caller passed `multimodal=` to `context_quality_signal` (see Finding 5) |
| 40 | Evidence Thumbnails | WORKS | `THUMBNAIL_EDGE = 96`; the 24,000-char cap is enforced on write **and** on read; only `data:image/…` is accepted, server-side and client-side |
| 41 | Video Ingestion | WORKS | ingest returns `status:"unavailable"`, `indexing_status:"skipped"`, `node_id: None`; nothing is stored |
| 42 | Frontend Reliability | WORKS | `CoreServiceUnavailableBanner`; `ActionButton` does not fire success callbacks for `ok:false`; vitest 100% on all four metrics |
| 43 | Trusted Agent Loop | WORKS | `LoopTrace`, the `loop` payload on both the streaming and non-streaming terminals, python-literal repair, the agent-eval CI gate |
| 44 | Command Center | **MISMATCH → code fixed** | `/api/command/briefing` returned `health: {available:false, grade:null, score:null}` on a Brain reporting `grade: "excellent"` (see Finding 1) |
| 45 | Evidence → Action | WORKS | `POST /api/evidence/actions` with no resolvable citations → 200 with an explicit reason, never a silent empty list |
| 46 | Run Explanation | WORKS | `"ok": code == "done"`, reachable only from `state == "DONE"`; rendered on web, VS Code and Telegram |
| 47 | Project Sessions | WORKS | `GET /api/projects` → 200; `/agent` accepts `project_id`; a `NEEDS_REVIEW` run never becomes a project `done` |
| 48 | Citation Precision | WORKS | `tests/unit/test_typed_chunking.py`, `tests/unit/test_citation_grounding.py`; `plain` chunking stays byte-identical to the legacy walk |
| 49 | Graph Relation Evidence | WORKS | `tests/unit/test_relation_evidence.py` |
| 50 | Funnel Alerts | WORKS | `GET /api/admin/funnel-metrics` → 200 with named counters, `rates: null` and `alerts: []` below the 10-sample floor |
| 51 | Frontend Payload | **MISMATCH → doc fixed** | `npm run check:bundle` measures **103.0 KiB** gzip initial JS against the 150 KiB budget; the doc had read "~99 KiB" since 9.9.9 (see Finding 2) |
| 52 | Permission Modes | WORKS | `GET /api/permission-mode` → `strict` default with `proposal_first: true`; circuit breakers mode-invariant; the selector renders the server catalog and blocks `bypass` until acknowledged. One wording nuance: the home dial uses a confirmation dialog rather than a checkbox — consent is still explicit |
| 53 | Network Boundary | WORKS | `local_only` default; `hard_block_metadata_flags` = `do_not_share/local_only/private/sensitive` applied with no mode branch; `POST /api/network-boundary/preview` answers in `local_only` and reports `would_block` |
| 54 | Hybrid Cloud Chat | WORKS | all seven named modules exist; the adapter raises before opening a socket without `LATTICEAI_CLOUD_API_KEY`; the local path is a pure early return |
| 55 | Cloud Memory Write-Back | **MISMATCH → code fixed** | the enqueue was implemented and unit-tested, but both live call sites constructed `CloudResponseIngestor(store=knowledge_graph)` with no review sink (see Finding 6) |
| 56 | Obsidian Vault Bridge | WORKS | driven end to end in the signed-in pass: `permission_required` + token → `/permissions/approve/{token}` → `status: "dry_run"`, `scanned 2 / notes 2 / tags 1 / links.resolved 1` (see Finding 7 for the anonymous case) |
| 57 | Selective Brain Network | WORKS (flag off) | `GET /api/knowledge-graph/share` → `enabled:false` with the flag named; all four mutating share routes → 403 with the reason. **The encryption half of this row was being changed by a parallel track while this audit ran — see Caveat** |
| 58 | Release Assets | WORKS | package/pyproject/tauri/vsix all 11.1.0; `check_current_release_docs`, `check_doc_status`, `validate_release_artifacts`, `check_release_evidence_bound` all clean. Rolling the markers to 11.2.0 is the release lead's step, not this audit's |

**Totals as swept: 51 WORKS, 3 PARTIAL, 4 MISMATCH.** Of the four mismatches,
one was fixed in code during the sweep (Command Center), two were fixed in the
docs (Brain Home, Frontend Payload) and one was reported to its owning track
(Cloud Memory Write-Back). All three `PARTIAL` rows were reported rather than
changed: each needed a code change in a file another track owned this sprint,
or a behaviour decision that does not belong inside an audit. One Known
Limitation was retired as no longer true.

**Dispositions after the sweep (v11.2.0 integration close).** The four rows the
audit could not touch itself were closed once the parallel tracks had landed —
Findings 3, 4, 5 and 6 are all fixed in code, each with a regression test that
drives the shipped entry point rather than the collaborator (see
`tests/unit/test_t8_integration_close.py`). The verdict column above keeps the
*swept* verdict with its disposition appended, because a point-in-time audit
that silently rewrites itself is worth nothing; `FEATURE_STATUS.md` carries the
current state.

## Findings

### Finding 1 — the Command Center's health section was reading a key nothing writes (FIXED)

`CommandCenterService._health_section` read the Brain's health out of a nested
`overall` block:

```python
overall = report.get("overall") or {}
"available": overall.get("score") is not None,
```

`BrainIntelligenceService.health_report()` — the only producer — publishes
`overall_score` and `grade` at the **top level**. So the briefing's health
section was permanently `{available: false, grade: null, score: null}` while
`/api/brain/health` reported `grade: "excellent"` on the very same graph, and
the `check-health` quick action below it could never fire.

User-visible: `DailyBriefingPanel` renders
`{health.available && health.grade ? String(health.grade) : "—"}`, so Today's
Briefing showed a permanent dash with no reason beside it — the exact failure
mode the 9.9.7 rule ("a `—` must state why") exists to prevent.

Why the suite stayed green: `tests/unit/test_command_center.py`'s `FakeBrain`
returned `{"overall": {"score": …, "grade": …}}` — a shape no real collaborator
has ever produced. The test and the code agreed with each other and neither
agreed with production.

Fixed in `latticeai/services/command_center.py` (read the real shape), the fake
corrected to match its real counterpart, and pinned by
`tests/unit/test_a3_feature_audit.py`, which wires the two **real** services
together — the only arrangement that can catch this class of drift.

### Finding 2 — the initial-JS figure had not been re-measured since 9.9.9 (FIXED)

FEATURE_STATUS claimed "~99 KiB gzip". `node scripts/check_bundle_budget.mjs`
measures **103.0 KiB** (index 66.1 + utils 29.2 + useQuery 7.6), against a build
`scripts/check_frontend_build_freshness.mjs` confirms is byte-identical to
source. The budget gate itself passes with room to spare — only the sentence was
stale. Lowered to the measured figure with the tool that measures it named.
The same stale number also appears in `ARCHITECTURE.md`.

### Finding 3 — an empty Brain grades itself 100 / "excellent" (FIXED after the sweep)

`health_report()` averages only the dimensions it could measure. On a
first-run Brain, freshness, connectivity and consistency all report
`status: "unavailable", score: null` and embedding coverage reports 100 (zero
of zero indexed) — so the composite is `overall_score: 100, grade: "excellent"`
from a single dimension over an empty graph.

Each dimension is individually honest; the composite is not. This is a
behaviour question rather than a documentation one (the row does not promise a
specific empty-Brain grade), and changing the score would move a number the
frontend and its tests render — so it is named here rather than changed inside
an audit.

**Disposition.** The lie was upstream of the composite: an index holding
nothing reports `coverage_ratio: 1.0`, which is 100% of nothing. That dimension
now reads `unavailable` with the reason `"no indexable items yet"` whenever
`source_items` is zero, so the empty Brain has *no* measurable dimension and
the existing `scores or None` path already returns `overall_score: None,
grade: null` — which the frontend already renders as 데이터 없음 / "No data
yet". Two additions keep the partial cases honest too: every unavailable
dimension now carries a `reason` (the graph could not be read vs. nothing saved
yet vs. no relationships yet), and the report gains a `coverage` block
(`measured` / `total` / `unavailable` / `partial`) so a verdict drawn from two
of four checks says so. When nothing could be measured a top-level `reason`
names each gap — the 9.9.7 rule that a "—" must state why.

### Finding 4 — `vector_freshness_breakdown()` has no surface (FIXED after the sweep)

The Vector Freshness row explains that "12 pending" hides the difference
between twelve never-embedded imports and twelve edits whose current answers
are quietly wrong, and names `vector_freshness_breakdown()` as the answer. The
method exists on the store and is tested — and has **zero product callers**.
`GET /api/brain/vector-freshness` still returns the aggregate
(`{status, pending_items, total_items, detail}`), so the distinction the
sentence sells is not visible anywhere in the product.

Either the endpoint should carry the breakdown or the row should say the split
is store-level only. The fix lives in store/API files owned by another track
this sprint, so it is reported rather than edited.

**Disposition.** The endpoint carries it. `BrainIntelligenceService.vector_freshness`
now returns the four-key contract from an unchanged private method and attaches
`breakdown` beside it when the store can compute one. The key is **omitted**
rather than zero-filled for every reason it cannot be had — graph off, an older
store without the method, an unreadable index, an empty answer — because a
zeroed backlog nobody measured is exactly the dishonesty the split exists to
remove. The frontend chip reads `pending_items` and is untouched.

### Finding 5 — `context_quality` never gains its `multimodal` key in production (FIXED after the sweep)

The Image Retrieval row says `context_quality` "gains a `multimodal` key only
when image nodes are really in the context". The mechanism is real and tested,
but the only call site that passes `multimodal=` to `context_quality_signal` is
`context_for_query(with_meta=True)`, which has no production caller. Both live
producers of `context_quality` — `api/chat_helpers.build_context_quality` and
`core/context_builder` — discard the hybrid result's `multimodal` block. Net
effect: on every shipped surface `context_quality` keeps the four-key shape
even when the answer cites `Image` nodes. Owned by the multi-modal track.

**Disposition.** Both producers pass it now. `build_context_quality` calls
`multimodal_signal(matches)` on **both** of its arms — the hybrid store and the
lexical-only fallback, since a picture found by keyword is still a picture —
and `retrieve_context_for_generation` does the same for the document path. The
present-only-when-true contract is unchanged: `multimodal_signal` returns
`None` for an all-text result set and `context_quality_signal` omits the key,
so an all-text answer is byte-identical to what 11.1.0 returned.

### Finding 6 — cloud write-back is never enqueued on the live path (FIXED after the sweep)

`CloudResponseIngestor.ingest` stages a `change_proposal` **only when a
`review_queue` sink is bound**. Both production call sites bind none:

```
latticeai/services/hybrid_chat.py:  ingestor = CloudResponseIngestor(store=knowledge_graph)
latticeai/services/hybrid_chat.py:  ingest_status = CloudResponseIngestor(store=knowledge_graph).ingest(plan)
```

With `plan.auto_commit` hardcoded `False`, `ingest()` returns
`{"status": "staged", "review_item_id": None, "written_nodes": 0}` — knowledge
extracted from a cloud answer is neither written nor queued. It is discarded
after the `hybrid_done` frame. The unit tests pass a `review_queue` directly to
the ingestor, so the wiring gap is invisible to CI. Related: the hybrid policy's
`auto_commit` flag is settable through `POST /api/network-boundary/policy` but
has no consumer.

The row's "is enqueued as a Review Center `change_proposal`" is therefore not
true as shipped. Threading a review sink through
`stream_hybrid_cloud_turn` / `run_hybrid_cloud_turn` / `maybe_hybrid_stream_response`
touches the chat router and the review-queue seam, both of which other tracks
were editing during this sweep — reported, not changed.

**Disposition.** Threaded, exactly there. Both turn functions take
`review_queue` and `auto_commit` as arguments (defaults `None` / `False`, so a
headless caller still stages nothing), `maybe_hybrid_stream_response` forwards
the sink and resolves the flag through the new
`chat_hybrid.resolve_hybrid_auto_commit`, and `/chat` supplies the live queue
through a new `AppContext.review_queue` **provider** — a callable rather than a
value, because `REVIEW_QUEUE` is produced in `phase_platform_features`, two
phases after the context is built, and a captured value would have been `None`
forever. `resolve_hybrid_auto_commit` treats an unreadable policy as the
default rather than as permission.

The reason CI could not see this — the unit tests handed the ingestor its
collaborator directly — is also the reason the new test drives `POST /chat`
with the dial on `cloud_allowed` and a fake adapter, and asserts the item
landed in the queue with `source: "change_proposal"`.

### Finding 7 — the local-file 401 is a gate, not a defect (CLEARED)

Worth recording because it looks like a defect and is not. In the default local
profile (`require_auth = false`), `POST /api/ingestion/obsidian` and every other
local-file route answer **401** to an anonymous caller:
`PermissionGateway.require_local_user` resolves the raw session identity and
does **not** fall through to the anonymous local owner that `require_user`
returns. Reading only that line suggests the vault bridge is unreachable out of
the box.

It is not. Driven end to end in the signed-in pass — register (first user
becomes admin) → login → `POST /api/ingestion/obsidian` →
`permission_required: true` with an `approval_token` →
`POST /permissions/approve/{token}` → re-post with the token →
`status: "dry_run", scanned: 2, notes: 2, tags: 1, links.resolved: 1`. That
sequence *is* the "standard local-read approval dance" the row describes.
Filesystem access deliberately demands a named account even where the rest of
the local API accepts the anonymous owner. Verdict: WORKS.

### Finding 8 — a retired Known Limitation

"The approval card under 작업 → 실행 still labels raw payload fields (`Action`,
`Action Label`, `User Email`) in plain mode" is no longer true. `Act.tsx`'s
`HumanPermissionDetails` renders a translated action badge, the target
filename, a free-text reason and a labelled requester line, with a regression
test forbidding raw i18n keys from reaching the DOM; the repo carries no
rendered `Action Label` / `User Email` literal. The screenshot the bullet cited
as proof — `output/release/v10.6.3/screenshots/09-automation-runs.png` — no
longer exists (release evidence now starts at v11.0.0). The bullet was removed;
carrying a fixed defect as a standing limitation is its own kind of dishonesty.

## prefix-static hermeticity (wp19 / wp36) — re-confirmed

`Config.from_env` resolves a *missing* static dir to `sys.prefix/static` when
that exists, which on a pip-installed machine it genuinely does. `_empty_prefix`
in `tests/unit/test_cov_wp19_build_phases.py` and
`tests/unit/test_cov_wp36_build_phases_late.py` neutralizes that so the
assertions describe the resolver rather than the runner.

Re-verified by simulating the release-runner condition for a whole session: a
pytest plugin pointed `sys.prefix` at a temp directory that **does** carry
`static/` (with a sentinel file), and the two suites plus
`test_cov_wp19_cli_entrypoint.py` were run under it.

* **81 passed.**
* The simulated prefix's `static/` still contained only the sentinel afterwards
  — no test wrote into it.
* The hazard itself was confirmed live under the same plugin: with a missing
  configured static dir, `Config.from_env` resolves to the fake prefix's
  `static/`. The hermeticity is load-bearing, not decorative.

## Caveat — a concurrently mutating tree

This sweep ran while two other tracks were writing to the same working tree.
Files under `lattice_brain/` (portability, sealed box, multimodal, ingestion,
graph schema) and `latticeai/services/model_*` were changing during the run, and
one intermediate state was observed directly (a partially-rewired
`latticeai/services/obsidian_bridge.py` raising `NameError` mid-refactor, and a
`KnowledgeGraphStore` attribute warning at boot). Rows 37–41, 55 and 57 are
therefore reported **as of this snapshot**; their owning tracks are the
authority on where they land. Everything measured for rows outside those files
was stable across repeated runs.

## Changes this audit made

* `latticeai/services/command_center.py` — read the health shape the Brain
  actually publishes (Finding 1).
* `tests/unit/test_command_center.py` — corrected the `FakeBrain` fixture to
  its real counterpart's shape.
* `tests/unit/test_a3_feature_audit.py` — new. Pins the two drift classes this
  audit found: a doc naming an endpoint the app does not serve, and the
  briefing reading a health score the Brain never published.
* `FEATURE_STATUS.md` — Brain Home (Brain Brief is on the dock, not the first
  screen), Frontend Payload (103 KiB, measured), and the retired approval-card
  limitation.
