# Lattice AI Feature Status (v12.0.0)

> **Status: canonical** — current-truth feature state, kept in sync with the
> current release.

Current release: **12.0.0 — Open House**.

This file describes the current product state and known limitations. Historical
change history is intentionally limited to 11.0.0 and later in `RELEASE.md` and
`docs/CHANGELOG.md`.

## Product Position

Lattice AI is a local-first Digital Brain. The durable asset is the user's
Brain: conversations, documents, decisions, memories, provenance, and Knowledge
Graph structure. Models are replaceable voices over that Brain.

The main product surface is not an admin dashboard. The current line keeps the
living Brain and composer in the first screen while the Brain becomes an
active steward of its own knowledge: it diagnoses health, surfaces
contradictions and stale knowledge, proposes consent-first consolidation, and
recalls with hybrid lexical+semantic evidence. The 9.8.0 line makes that
grounding honest: retrieval reports context quality, ingestion scores
extraction quality behind an observe-mode gate, and vector freshness is tracked
and reindexable. The 9.9.0 line hardens trust: change proposals are
conflict-checked against the original content hash and applied atomically,
every mutating tool is inventory-governed with a fail-closed CI gate, the agent
verifier fails closed into review rather than reporting false success, and
device analysis no longer fabricates a "ready" model card on probe failure. The
9.9.6 line makes the Brain the same everywhere: the editor and the chat bot
report the same grounding verdict and the same plain-language run outcome, an
answer's evidence turns into one-click follow-ups, and work that spans several
runs keeps its state in a project session. The 9.9.7 line closes the last
recorded gaps — no `✖` remains in the surface parity matrix, and every
remaining `—` states why it is a design boundary. The 9.9.8 line makes autonomy
explicit: a `strict` / `trusted` / `bypass` dial widens what runs without an
extra approval prompt, scoped per user and per workspace, while hard circuit
breakers stay mode-invariant. The dial is set in 설정 → 혼자 해도 되는 일 (renamed from 에이전트 자율성 in 10.5.0);
the default stays `strict`, so behaviour is unchanged from 9.9.7 until a user
deliberately raises it. The 10.0 line makes the product legible to someone who did not build it:
the first screen is four zones, capture lives in the composer, and both
languages are complete. 10.0.1 carries that inward — the agent loop module
holds the loop and nothing else, with its state vocabulary and pure helpers
in single-source sibling modules. The 10.1.0 line adds a local-first hybrid
path: the Knowledge Graph never leaves the machine, while a cloud LLM can be
hired as an opt-in worker that reads a minimal extracted slice. The default
boundary is `local_only`, cloud requires an explicit acknowledgement,
sensitivity filters are mode-invariant, and cloud-derived memory enters the
Review Center as a proposal rather than being written. In 10.1.0 that dial is
reachable through `/api/network-boundary` and environment configuration only —
there is no in-app control yet. The 9.9.9 line made the shell lean: copy
follows its route instead of the entry chunk, cutting first-paint JavaScript
by a third. The 10.5.0 line names every plain-mode surface in the reader's
words rather than the engine's: the autonomy dial is three sentences instead of
three mode names, a file's path into memory is three named steps instead of two
API payloads, and runs carry the name their author gave them instead of a
database id. Nothing was removed — every engineering panel is one mode switch
away — and the release screenshots are now captured in `basic`, the mode a
first-run user actually lands in. The 10.6.0 line changes where those surfaces
sit rather than what they are called: each main screen used to open as a row of
equal tabs, and each now opens on the panel that answers the question that
brought the reader there — Capture on one 자료 추가하기 card holding all three
intake methods, Work on 검토함 rather than an empty goal composer, the model
library on which model is running and how to switch it, the Brain home on a
single bordered station instead of five stacked blocks. Everyday destinations
(대화 · 자료 · 기억) and management destinations (작업 · AI 모델 · 설정) are now
separate lists, the second rendered from one array into either the topbar or the
menu by a single breakpoint. Nothing was deleted; every panel is still on its
page, below the one that leads it. The 10.6.1 line finishes that pass on the
five screens it did not reach: sign-in is one card with the promise bar demoted
to a hairline strip beneath it, the recommended model is one hero card instead
of a CTA duplicating the first row of its own list, the Brain home leads with
the composer and drops add-material and autonomy to the station floor, the runs
tab stacks by urgency (승인함 → 설치된 자동화 → 실행 기록), and a review item is
evidence on the left with the approve/reject decision beside it rather than
below a diff. Each of those orders is now asserted by a unit test rather than
described. The 10.6.3 line takes one of those five back. The Brain home had been
reordered but not re-shaped: the greeting still ran a 5.4rem organism and a
centred headline down the middle before the composer, and the three suggestions
sat *between* the composer and its own toolbar, so the alternative to typing
wore the same border as typing. The screen is two surfaces now — a station
(compact greeting banner, composer, one toolbar for material and autonomy) and a
separate deck below it holding the suggestions. Nothing left the screen; the
second choice simply stopped sharing a card with the first.

The 10.10.0 line is the quiet station: the home canvas holds the composer, and
everything else became an affordance that opens on demand. The memory stats
are a hover badge with a summary-graph popover, the six capture chips fold
behind the composer's +, the model banner is a status pill on the hero's right
edge, and past conversations / stats / the memory map live on a dock rail
whose drawer is focus-trapped and portaled over the screen. The same release
raises the frontend test floor to 100% on all four coverage metrics (enforced
by vitest thresholds and CI) and trims public release history to start at
9.0.0. The 10.9.0 event-loop guarantees stay in force: ruff's `ASYNC`
blocking-call rules and `tests/unit/test_event_loop_not_blocked.py` keep long
work off the loop.

The 11.6.0 line is the last structural one: **the product has one door, and it
is Rust.** `lattice-host` serves every product route natively and supervises a
Python **AI worker** that does only what a model does. Nothing about the
product's promises moved — the Brain is still the durable asset, the dials still
mean the same things, and the screens are the ones 11.5.2 shipped — but the
process that answers a request changed, and two surfaces left with the platform
code that became the worker: the **Telegram bridge** and the **SSO/OIDC login
and callback flows**. Both are recorded below with their reasons rather than
quietly dropped, and the gaps this release carries openly are listed in Known
Limitations.

The 11.7.0 line is the clean sweep on top of that door: the three oracle bugs
11.6.0 ported as-is (empty command-search knowledge, snooze 500, double-reject
500) are fixed; the §5.3 holes (UTF-8-only upload enrich, primary-node
vectors, silent user hooks, unsanitized native writes, missing review
timeline events, two `workspace_os.json` writers) are closed; Self-Model
writes, the xlsx export, chat `ingest_generated` and vault-watch — all
stranded on retired seams — are native; replay clocks no longer detonate on
a calendar date; and the SPA is restyled on an elevation ladder (no glass).
What is still open is named in Known Limitations rather than described as
"three leftovers" that no longer exist.

The 11.8.0 line takes weight off that same door rather than adding to it. The
worker went from **28 routes to 19** — nine routes that no caller in the tree
reached were deleted with their modules, their allowlist entries and the
`pypdfium2` dependency one of them pulled in, and negative tests now assert the
gateway answers `404` instead of forwarding. The Rust crates lost their blanket
`#![allow]` headers (about 191 files) and the ~650 diagnostics underneath were
fixed at the source; two 702-row decision grids became named unit tests; the
dead Python halves of chunking, permissions and password handling are gone. One
real bug surfaced in the process: the worker had been reading `sessions.json`
only at boot, so a login after worker start was invisible to it. Two things got
*smaller* rather than better and are recorded as such below — the Python
coverage gate (100% lines+branches → line 90) and the local lint chain
(13 → 10 gates).

The 11.9.0 line puts that door in working order. Thirteen surfaces the docs
already called Current now actually answer — model recommendations, setup
probes, computer-use status, agent eval, a live single-agent run, automation
mining, the workflow executor, `build`/`deploy_project`, backup blobs. The
hybrid cloud lane is wired (ReviewSink + shape-only EgressAudit, dual
credentials, escalation policy). MCP is a real JSON-RPC server. The 8GB-tier
default (gemma-4-e2b) runs, and chat file generation — the v9.2.0 headline
deleted in the 11.6.0 port — is restored.

The 12.0.0 line opens the house. The owner's first ask for this release was
**complexity management**, so the two largest crates are grouped by what a
file is *for*: `lattice-agent` into `kernel` / `parse` / `content` / `tools` /
`surface` / `prompts` (43 files, `git mv` only), and `lattice-platform` into
seven domains — `workspaceos`, `toolsurface`, `governance`, `adminops`,
`knowledge`, `modelops`, `shell` (100 `git mv`). Each crate carries its own
`ARCHITECTURE.md`, each group's `mod.rs` states its invariants, and each
`src/lib.rs` keeps a compatibility map so no import path broke.
`docs/DEVELOPMENT.md` is now a contributor onboarding path (10-minute
quickstart, a where-does-it-go table, a guide to the gates) and
`docs/ROADMAP.md` is a new prioritized list of what is still open. On top of
that: four gaps this file used to list are closed (live restore, consented
setup execution, `POST /mcp` inside the contract, declared pointer tools),
Graph RAG gained Korean-aware quality and a large speed pass, and the agent
harness stopped requiring JSON from models that cannot produce it. What is
still open — including small-model *content* quality and the mock-only
`api_key` path — is named in Known Limitations.

## Current Feature Status

| Area | Status | Notes |
| --- | --- | --- |
| Crate Domain Structure | Current | **12.0.0's headline.** `lattice-agent` is six groups — `kernel/` (the loop and every decision that can refuse), `parse/` (untrusted model text → typed values), `content/` (the bytes a run is about to write), `tools/` (what a tool does and the ground it may do it on), `surface/` (HTTP in/out plus the worker client), `prompts/` — over 43 moved files. `lattice-platform` is seven domains — `workspaceos`, `toolsurface`, `governance`, `adminops`, `knowledge`, `modelops`, `shell` — over 100 `git mv`. Every move is a rename: the frozen goldens and contract tests answer identically before and after, which is what makes "zero behaviour change" checkable. Each crate ships its own `ARCHITECTURE.md` (`rust/lattice-agent/ARCHITECTURE.md`, `rust/lattice-platform/ARCHITECTURE.md`), each group's `mod.rs` states what belongs in it and what must never, and each `src/lib.rs` ends with a compatibility map so every pre-12.0.0 import path still resolves. What this is **not**: a decoupling. The four couplings that cross a domain line in `lattice-platform` are named in the relevant `mod.rs` rather than removed. |
| Contributor Onboarding | Current | `docs/DEVELOPMENT.md` was rewritten for 12.0.0 as the path in: a 10-minute quickstart (venv → `npm ci` → `npm start` → `/app`), an 어디에-무엇을 table that routes a change to the crate and group that owns it, and a guide to which gate goes red for which mistake. `docs/ROADMAP.md` (new, reference status) carries the prioritized open-gap list with difficulty and a proposed path per item, including which items closed in this release so they are not re-opened as work. |
| Brain Home | Current | The Living Brain and the composer are in the first viewport on desktop (1280×800) and mobile (390×780), asserted in `tests/visual/v3.home.spec.js`. The Brain Brief is **not** on that first screen: 10.10.0's quiet station moved it into the dock's 통계 drawer, so it opens on demand like the other affordances. **11.8.0 rebalanced the canvas.** The composer is the hero; the Living Brain is three times larger (60px → 179px at 1440) and accretes gold and jade growth rings as memory accumulates, with a '기억이 자라고 있어요' caption bound to the same readiness state the organism reads — the caption never claims growth the readiness score does not report. The layout is a full-canvas grid (Brain left, composer and starter pills centre, suggestions right) with a floor continuity bar carrying 지난 대화 · 현황 · 기억 지도 · 기능, which previously sat under the fold. The organism palette was harmonized onto the ink + jade + gold tokens, and the growth animation has a `prefers-reduced-motion` fallback that conveys the same state without movement. |
| Automation Intelligence | Current | `/api/automation` mines recurring user questions from `conversation_messages` (deterministic, Korean-friendly question mining, literal-question evidence) and connected knowledge folders into one-click suggestions; installs are idempotent, disabled-draft, review-queue-gated workflows. **11.9.0 made the mining real** rather than a documented stub. |
| Workflow Designer | Current | Workflow run is a real per-step executor with terminal states. Resume honours the approval gate (fixed in 11.9.0). Review Center `run_now` is wired to the same executor. |
| Brain Intelligence | Current | The Brain diagnoses itself: /api/brain health scoring (freshness, connectivity, search readiness, consistency), proactive insights digest, contradiction surfacing, and consent-first duplicate consolidation, wired from the lattice_brain quality layer and covered by unit + live-boot tests. The composite is honest about what it could not measure: every unavailable dimension states its reason, a `coverage` block says how many of the four were measured, and a Brain with nothing to measure reports no score and no grade — an empty Brain never grades itself "excellent". |
| Temporal Knowledge | Current | Nodes and edges carry `valid_from` / `valid_to` / `superseded_by`, added by an idempotent additive migration on an existing Brain (NULL means "since `created_at`" / "still true" — never an empty string, and no backfill). `store.as_of(timestamp)` returns the graph slice that was valid at that instant, and `neighbors(..., as_of=…)` takes the same slice; both default to today's behaviour when the argument is omitted. |
| Proactive Synthesis | Current | The Brain notices on its own and asks rather than acts: contradicting memories, recurring-but-unnamed topics, always-together-never-linked pairs, and decayed episodic fragments all arrive as Review Center proposals (`kg_change_digest`) with a plain-language explanation. Deterministic — token overlap and clock arithmetic, no model needed; a model may only reword the weekly brief. Runs event-driven: the ingestion pipeline's audit seam hands every landed ingest to `BrainIntelligenceService.note_ingest`, and a pass fires every `LATTICEAI_SYNTHESIS_THRESHOLD` (25) genuinely new nodes — duplicates do not count, and a trigger that fails never fails the ingest. **Every write goes through approval**: `POST /api/brain/contradictions/resolve` approves the proposal first and only then stamps the pair's validity windows (keep / replace / keep both with time ranges), and the same pair is never proposed twice while it is still waiting. The *automatic* trigger is **toggleable from the home dock's 기능 drawer** since 11.2.0 (`LATTICEAI_SYNTHESIS`, default on); turning it off stops the Brain deciding *when*, and an explicitly requested run still works. |
| Memory Decay | Current | `GET /api/brain/importance` scores each memory by use (ingested access counts, else the store's own read counter) plus recency decay, and names the weakest *episodic* fragments only — a decayed Decision or Document is reported as stale knowledge, never folded away. `/api/brain/quality-report` carries the same numbers plus a `tidying` flag so "the Brain is tidying up" is visible rather than a background surprise. |
| One Door — the Rust product server | Current | **11.6.0 completes the migration.** `rust/` is nine crates and `lattice-host` *is* the product server: `mount_table()` declares **422 native operations across 41 route families** (11.9.0's 420 plus `POST /mcp` and `POST /api/ingestion/folder/prune`), mounted at the paths they always had, and a unit test proves no `(method, path)` is claimed twice before the router is built. Python is a pure-compute **AI worker**, serving **20 routes** since 12.0.0 (LLM + stream, embed, extract, parse, four renderers, ASR, the HNSW vector-query seam, the model/engine catalog, the embeddings status probe, `/health`) after nine caller-less routes were deleted end to end in 11.8.0 and one was added back for opt-in ANN search; the door forwards only what the committed allowlist `rust/fixtures/worker_allowlist.json` names — generated from the worker's own profile, `include_str!`-compiled into the binary, pinned from Python by `tests/unit/test_worker_allowlist.py` — and answers `404 {"detail":"Not Found"}` for everything else. **Every write is native**: `lattice_core::graph_write` owns ingest, curation, provenance, taxonomy and the vector queue, held to Python's bytes by a 32-step row-parity battery (every table dumped after every step, zero tolerated differences) and a `sqlite_master` comparison over all 67 objects; 17 graph tables changed owner and `db_write_ownership.rs` asserts no graph table is worker-written. The surface itself is replayed rather than re-described: **1,487 recorded HTTP cases** across twelve committed fixture files, captured from the real Python app while it still served them. Earlier phases still hold — retrieval/chunking/agent-kernel/agent-loop goldens are unchanged and green, and the desktop still fronts through this gateway. Roadmaps: docs/v11.4.0_RUST_FOUNDATION_PLAN.md, docs/v11.5.0_RUST_COMPLETE_PLAN.md, docs/v11.5.1_RUST_FULL_LOOP_PLAN.md, docs/v11.5.2_TIGHT_SHIP_PLAN.md, docs/v11.6.0_ONE_DOOR_PLAN.md. |
| Brain Chronicle (연대기) | Current | A seventh primary screen (`#/chronicle`, alias `#/timeline`) that turns the Brain's growth into a timeline. Read-only over existing tables — `GET /api/chronicle/overview` (day-bucketed totals + sparse activity series in the app timezone), `GET /api/chronicle/day/{date}` (the day's story: sources, new concepts, conversations, changed facts — group lists capped at 200 with true totals in `counts`), `GET /api/chronicle/as-of` (graph slice stats + top entities at any past instant, via `store.as_of()`). The UI is a hand-rolled SVG growth curve with a keyboard-operable time handle (ARIA slider), a week×weekday activity heatmap, plain-language day cards deep-linking into memory search / graph / conversations, and a rewind panel ("그때 중요했던 개념"). First surface to expose the 11.1.0 temporal columns. No writes, no schema change, no model calls; an empty Brain shows an honest empty state. |
| Hybrid Recall | Current | /api/memory/recall and the graph-layer `hybrid_search` blend lexical evidence with vector similarity (hybrid-evidence/v2 gate) with workspace-scoped vector hits and honest lexical fallback when the vector tier fails. Chat consumes a `context_quality` signal so grounding reflects how strong the retrieved context actually is. **12.0.0 made the Korean path aware of its own grammar**: josa stripping is two-stage (compound particles included), so 「프로젝트의」 and 「프로젝트를」 reach the same candidates; an **evidence gate** stops a lone lexical match from counting as grounding; and containment dedupe keeps a long chunk from being counted twice alongside the shorter chunk inside it. |
| Section Tree | Current | The typed chunker already knew each chunk's `heading_path` (`아키텍처 > 저장소`), but through 11.9.0 that string stopped at chunk metadata: nothing in the graph said a document *had* sections. 12.0.0 writes the outline as the tree it always was — `Document ←PART_OF— Section ←PART_OF— Section`, and `Section —HAS_CHUNK→ Chunk` — so "which section did this fact come from" is answerable and so is "what else is in that section". No schema widening: `Section`, `PART_OF` and `HAS_CHUNK` were already in the taxonomy and simply had no writer. A document with no headings produces **no** sections — one fabricated "제목 없는 절" per file would put a node in the graph naming something the author never wrote. Measured on the release corpus: **549 of 555** triples carry a section source. |
| Typed Relation Production | Current | Extraction now *produces* `PART_OF` and `CONTRADICTS` rather than only declaring them in the vocabulary, edges are directed and typed at write time, and evidence is classified (verb-backed vs. co-occurrence) so a relation carries where it came from. |
| Folder Ingestion | Current | `ingest_folder` indexes a chosen local folder with `.latticeignore` filtering; long runs execute as resumable background jobs surfaced through `/api/ingestion/jobs` rather than a single blocking request. **11.9.0:** the trusted owner is accepted and approval tokens are unified (`LocalApprovals`, redeemable at `/permissions/approve`). **12.0.0 made re-indexing nearly free.** Every file carries a fingerprint — path + size + mtime, with sha256 computed only when size or mtime moved — stored in `ingestion_provenance.metadata_json` rather than in a second table, because the absolute path is already that row's `source_uri`. An unchanged file is not read, chunked or embedded: a no-change reindex went **33s → 0.26s** with the waste ratio (work redone for no new knowledge) **1.00 → 0.00**, and a first index went 25.8s → 7.2s. Vault-watch uses the same skip. |
| Deleted-file Cleanup | Current (consent-gated) | Incremental ingest **counts** vanished files and leaves their nodes — dropping a node is a product decision, not a side effect. `POST /api/ingestion/folder/prune` is the explicit door: without `confirm` it is a dry-run naming the files and the node/edge/chunk/vector counts a prune would remove; with `confirm` it removes each document subtree through `delete_document_tree`, which clears edges in both directions (verified: zero dangling edges afterwards). `GraphWriter::delete_node` — which leaves `PART_OF` behind, the 11.7 trap this list has carried since — is never used on this path. The SPA surfaces it as a 「삭제된 파일 정리 (N)」 card button. Nothing on disk is ever touched. |
| Extraction Quality | Current | Ingestion scores per-source `extraction_quality` and runs an observe-mode `quality_gate` that flags low-quality extractions instead of silently accepting them. |
| Vector Freshness | Current | `/api/brain/vector-freshness` reports embedded-vs-total content so stale embeddings are visible and reindexing can be triggered on demand. The same response carries an additive `breakdown` — embedded / missing / stale / queued, from `vector_freshness_breakdown()` — because "12 pending" hides the difference between twelve never-embedded imports and twelve edits whose current answers are quietly wrong. A store that cannot split its backlog simply omits the key; the four keys the freshness chip reads are unchanged. |
| Vector Index Backends | Current | `LATTICEAI_VECTOR_INDEX` selects `brute` (default, exact, byte-compatible with every previous release), `quantized` (int8 storage, exhaustive, approximate scores) or `hnsw` (approximate nearest neighbour, needs `pip install "ltcai[hnsw]"`). An unknown name or a missing extra falls back to the exact scan **and says why** — in `index_status().storage.vector_index` and in each search result's `index` block. Approximate backends set `approx: true`, which reaches `context_quality`. Measured at 10k and 50k vectors in `docs/PERFORMANCE.md` with `scripts/bench_vector_index.py`, which reports recall@10 against the exact scan next to every latency number: hybrid p50 **10.1 ms at 10k / 43.9 ms at 50k** with HNSW (target was < 50 ms at 10k) against 299 ms / 1515 ms for the exact scan, at 0.95–0.99 recall. **Selectable from the home dock's 기능 drawer** since 11.2.0, where an uninstalled `hnsw` is disabled with the reason instead of being offered and quietly ignored. **12.0.0 turned `hnsw` into a real search path**: with `LATTICEAI_VECTOR_INDEX=hnsw` the native search asks the worker sidecar (`POST /worker/vector/query`) for `k * 8` candidate ids capped at 200, then scores exactly those rows with the same cosine the brute path uses — approximate recall, **exact ordering** — and reports itself as `hnsw+rescore`. Any failure (no sidecar, missing extra, empty answer) falls back to the exact scan **carrying its reason**. The sidecar also appends incrementally instead of being invalidated by every write. The default is still `brute`, which needs nothing installed anywhere and never calls the seam. |
| Background Embedding | Current | A failed inline vector sync now queues durable work (`vector_jobs` in the brain database) instead of leaving `indexing_status: "pending"` for a human to notice. One tick drains a bounded batch, retries are bounded, and a node that exhausts its budget stays visibly `failed` with its last reason. Caller-driven by design: the queue survives restarts, so *who* runs the worker is a deployment decision, not a side effect of ingesting — since 11.6.0 that caller is `lattice-jobs`' scheduler, holding a `GraphWriter` and draining natively. The inline sync itself (`LATTICEAI_AUTO_VECTOR_INDEX`, default on) is **toggleable from the home dock's 기능 drawer** since 11.2.0. **12.0.0 moved the embed call ahead of the write transaction** — the writer no longer holds a lock while a model runs — taking drain throughput from **~66 to ~1,300 items/s**, and made the scheduler adaptive (busier when there is a backlog, quiet when there is not): a 991-item backlog that took ~40 minutes now drains in **15.3 seconds**. Embedder selection is auto-detected: a real downloaded model is adopted when one is present, and the hash model is labelled **fallback** rather than presented as semantic. Vector identity is filtered by `(model, dim)`, so two embedding spaces cannot quietly mix. |
| Fusion Strategy | Opt-in (default off) | `LATTICEAI_FUSION_STRATEGY=rrf` fuses channel *positions* (Reciprocal Rank Fusion) instead of the lexical channel's `1/rank` and the vector channel's normalized cosine, which are not on comparable scales. Per query class via a JSON object. Off everywhere by default: alpha fusion is the ranking this release's assertions describe. 11.2.0 adds the one-switch form (`LATTICEAI_FUSION_RRF`), **toggleable from the home dock's 기능 drawer**; a per-class pin still wins over it. |
| Graph Candidate Expansion | Opt-in (default off) | `LATTICEAI_GRAPH_EXPANSION=1` adds the one-hop neighbours of the strongest hits to the candidate pool, so a node one edge away from the match is reachable at all. Capped at 5 candidates from 3 seeds, scored at half its seed's score, and reported in the result's `graph_expansion` block (seeds walked, candidates added, whether the cap bit, seeds that failed). **Toggleable from the home dock's 기능 drawer** since 11.2.0. |
| Feature Toggles | Current | Every opt-in feature is a switch in the home dock's **기능** drawer (`GET/POST /api/features`, `latticeai/services/feature_toggles.py`) instead of an environment variable and a restart. The catalog is **server-rendered** — ids, labels, one-line explanations, defaults, and which choices are installable — so the panel cannot drift from what the server honours. Precedence is **user → env → default**, and the panel says which one answered: a switch nobody has moved still follows its environment variable (and its `FeatureGate` override) exactly as before, because the bound resolver has an opinion only where a person made a choice. Changes persist as atomic JSON under the data dir and take effect immediately — every catalogued switch is backed by a per-call gate, and the catalog reports `live` rather than asserting it. Covers `allow_multimodal`, `video_ingest`, `vault_watch`, `brain_network`, `synthesis`, `auto_vector_index`, `auto_late_fusion`, `fusion_rrf`, `graph_expansion`, and the `vector_backend` choice — where an uninstalled `hnsw` is shown **disabled with the import's own reason**, and refused by the writer, rather than hidden or silently downgraded. |
| Self-Model (Personal Ontology) | Current (API only) | The Brain keeps a small, separately-governed subgraph about *its owner*: `Self` / `Preference` / `Habit` / `Relationship` node types (plus the existing `Decision`), rooted at `self:root` with `PART_OF` edges. **11.7.0 restored the writes.** From 11.6.0 until this release the five mutating ops posted into the retired `/worker/graph/mutate` (404 on every install) and `resolve_contradiction` claimed "applied" while writing nothing; they now go through `self_model_write` + `GraphWriter`, and the nine recorded fixture bodies match byte-for-byte. Extraction is still deterministic — first-person Korean and English phrasings, no model required — and **only ever proposes**: `POST /api/memory/self-model/propose` files each candidate in the Review Center, and `POST /api/memory/self-model/apply` writes it *after* the approval returns. The user owns it: `GET /api/memory/self-model` shows every fact and the exact summary that gets injected, `POST` corrects one, `DELETE /api/memory/self-model/{node_id}` forgets it — both direct, because it is their own profile. A summary rides along with document-generation context (empty profile → nothing injected, never more than half the context budget). Still no dedicated screen, still no wording `refiner`, and `open_keys` still accepts `pending` only. |
| Workspace Reorganization | Current (proposal-first) | "이 프로젝트를 정리해줘" produces a plan, not a tidy-up: `WorkspaceOSStore.propose_reorganization` asks the graph what each file is about and stages **one** `change_proposal` (kind `folder_reorganization`) describing every move into `topics/<주제>/`. Files the Brain cannot justify are reported as `unplaced` with a reason instead of being swept somewhere plausible, and **no deletion is ever proposed** — the planner has no delete path at all. Approving from the Review Center applies it through the same `ChangeProposalService` door every other staged change uses; a move whose source vanished or whose target now exists is skipped and reported, never forced. |
| Change Governance | Current | `core/tool_governor.py` `MUTATING_TOOL_INVENTORY` requires every mutating tool to be governed or explicitly exempt (release-checked). File edits/deletions flow through change proposals that record a base content hash and re-check it for conflicts before applying atomically. The loop verifier fails closed to `NEEDS_REVIEW` on unverifiable or failing outcomes; it is `lattice-agent`'s since 11.5.1, and 11.8.0 deleted the last Python remnant of the old harness (`scripts/agent_eval.py`) rather than leave a script that no longer gated anything. |
| Brain Brief | Current | MemoryService turns real workspace, conversation, graph, vector, and source-health signals into focus, evidence, and next actions. `GET /api/brain/proactive-brief` adds the proactive section — what the Brain noticed and what is waiting for a decision — read-only, counting the proposals already in the Review Center rather than raising new ones. |
| Conversation | Current | Chat is the primary action. It refuses to fake model output when no model is loaded, surfaces memory proof when context exists, and routes explicit file actions into the governed workspace file tool. **11.9.0 restored chat file generation** (the v9.2.0 headline, deleted in the 11.6.0 port): 「index.html 만들어줘」 produces a real model-authored file — per-extension anchored prompts, judge + one corrective retry, honest sanitize labeling (valid/repaired → SPA badge), ≤3 files sequential for projects, real docx/pdf via the worker render seam, honest xlsx/pptx refusal naming the agent path. Chat / memory / chronicle / command now see knowledge (null workspace = personal visibility; writers stamp `"personal"`). |
| Knowledge Graph | Current | Memory graph exploration, graph read compatibility, provenance-aware retrieval, fail-closed workspace reads/traversal, explicit legacy-global compatibility, workspace-safe duplicate content, and KG v2 equivalence gates remain active. |
| Source Capture | Current | Files, folders, notes, and web/source capture paths feed Brain memory and graph context through explicit user actions and the unified ingestion pipeline when available. **11.7.0:** `/upload/document` sends non-UTF-8 / known-binary bodies through `POST /worker/parse`; upload, browser-tab, garden-note and chat-turn doors batch-embed chunks when `(model_id, dim)` agrees. Vault-watch is a real poller joined to native note ingest (watched PDFs parse the same way). `POST /knowledge-graph/ingest` stays text-only by contract. A provenance record is keyed by its origin (node, content hash, source type, source URI, pipeline), so re-scanning an unchanged folder or vault updates one record per source instead of appending a duplicate — through 11.0.x the key included a wall-clock second, which made that behaviour depend on how fast the machine was. **12.0.0** gave that same row the ingest fingerprint (size + mtime + content sha256 in `metadata_json`) so an unchanged file is skipped outright, and taught the writer to emit the document's section tree alongside its chunks. |
| Local Models | Current | Setup and model recommendation flow remains explicit; model downloads and runtime installs require user action. **11.9.0:** `/models/recommendations` is a native RAM/AS probe plus the worker catalog and a RAM-tier `top_pick`, not a stub. The 8GB-tier default `gemma-4-e2b-it-4bit` actually runs (compact profile: `e2b`/`e4b` → compact; MoE `a4b` markers stay safe). The catalog is the 2026 generation, re-measured against the Hugging Face API on 2026-08-10: **Gemma 4** (E2B / E4B / 12B / 26B A4B / 31B), **Qwen3.6** (27B dense, 35B A3B MoE), **Qwen3.5 9B**, plus two text-only entries that own real tiers — **LFM2.5 2.6B**, the only model that runs comfortably on 8GB, and **GPT-OSS 20B**, the most-downloaded entry here. Each row states its own modality, so "reads pictures" is never implied. Repo ids are stored in the Hub's canonical casing (`gemma-4-12B-it-4bit`, not `-12b-`) so the download path, the on-disk cache directory and the catalog key are one string. **12.0.0 fixed a loading bug that a name roster caused**: model loading gated on a list of known names, so a Qwen AWQ build could not be loaded at all — not "loaded and performed badly", but refused before it started. |
| Model Lifecycle | Current | The registry keeps **two lists**. *Recommended* entries are offered, listed and downloadable. *Recognised* entries (Qwen3-VL 4B/8B/30B, Qwen2.5-VL 7B, Llama 3.2 11B Vision, Llama 4 Scout, the Gemma 4 base builds) are superseded but real: never offered and never recommended, yet still resolvable so weights a user already downloaded keep their name, size and runtime profile instead of showing up as an unknown blob. Models that are **gone from the Hub** (`phi-3.5-vision-4bit`, `moondream2-4bit`) or **gated** (`google/gemma-3-*`, `meta-llama/*`) are deleted from both lists — recognising something nobody can obtain is noise, not compatibility. `model_compat` gained the matching families (`gpt_oss`, `lfm2`) and architecture map (`qwen3_5`, `qwen3_5_moe`, `gpt_oss`, `lfm2`, …) so a recognised model never falls back to the vision-less "unknown" profile. |
| Model Verification | Current (static, not a load test) | `scripts/verify_hf_model_registry.py` re-measures every entry — recommended and recognised — through the public HF API: existence without credentials, gated flag, canonical casing, `library_name`/tags, config architecture, sibling files and their exact byte sum against the registry's recorded size. It **never downloads weights and never loads a model**, and no flag exists that could; the refreshed `verification_report.json` ships with the tree (18/18 present, 18/18 statically loadable, 0 bytes downloaded). The verdict is explicitly **static** — MLX library signal + an architecture with a loader in mlx-lm/mlx-vlm + community downloads — which means "nothing published rules out a load", **not** "this loaded". It cannot see a corrupt shard, an incompatible quantisation, a tokenizer mismatch, or an installed mlx-vlm older than the architecture. The loader plus the on-device smoke test remain the only authority on whether a model really runs. |
| Installer Audit | Current | Setup Wizard, auto setup, and engine installers expose redacted command plans, require confirmation tokens, and write local process audit events. **11.9.0:** `/setup/scan` and `/setup/auto` are real probes. **12.0.0: `/setup/install` actually installs — on explicit per-item consent.** It runs brew/pip/uv only when the request names an item that is on a **server-derived** allowlist (the command is derived from the plan the server itself produced, never taken from the request), with a bounded timeout and captured stdout/stderr; a name that is not on that list is refused as "not an allowlisted brew/pip/uv item". The default path is still manual — nothing installs because a page was opened — so this replaces the honest refusal with an honest, consented execution rather than with automation. |
| Cloud Models | Opt-in | Cloud prompts are sent only after credentials resolve and the user (or the escalation policy) selects a cloud path. Dual modes since 11.9.0: `api_key` (OpenAI-compatible, mock-server verified only) and `cli_oauth` (locally OAuth-authenticated `agy` / `grok`). See Hybrid Cloud Chat. |
| Telegram | **Removed in 11.6.0** — reason stated | The bridge lived in the platform code that became the AI worker (`latticeai/integrations/telegram_bot/`, the lifespan bridge start, the CLI notification). With the product server in Rust there is no in-process product for it to bridge to, so it was deleted rather than left as a half-wired surface. `latticeai.integrations.telegram_bot` is on `wheel_smoke.py`'s not-importable list, and `telegram_chats.json` is why `Owner::Worker` survives as an enum variant. Not a judgement about the feature — a consequence of the boundary. |
| Agent Runtime | Current | AgentRuntime preview/readiness contracts avoid tool execution during preview, reject unknown roles, require explicit human approval for non-auto-approved plans, tolerate legacy run events with contract envelopes, and expose orchestration boundaries. **11.9.0:** `POST /agent` reaches the native loop and carries the product policy table; `/agents/api/run` is a live single-agent pass with honest health. |
| Computer-use Status | Current | Status is a real worker capabilities probe (`pointer_tools` on `/worker/sysinfo`, additive with `python_version`). The six pyautogui pointer tools still execute in the worker; a stock install answers unavailable. **12.0.0 declared how to get them**: `pip install "ltcai[pointer]"` is a named extra in `pyproject.toml`, so the capability is a documented install choice instead of an undocumented gap between "unavailable" and a hand-installed dependency. Closing the remaining half still needs a native actuator — a decision, not an oversight. |
| Tool Registry / MCP | Current | ToolRegistry diagnostics, explicit desktop/knowledge/network policy gates, and MCP install state are separated from app-factory helpers. **11.9.0:** `POST /mcp` is a real streamable-HTTP JSON-RPC server (`initialize` / `tools/list` / `tools/call`) exposing a curated safe tool set plus seven skills with parsed schemas; governance refusals are JSON-RPC errors; `/mcp/call` really dispatches; `/mcp/install` is honest (enables skills/plugins, remotes stay manual). `build` / `deploy_project` really run governed scripts. **12.0.0 brought `POST /mcp` inside the OpenAPI product contract** as a single JSON-RPC envelope operation, declared in the mount table like every other product route — it was outside the contract by design through 11.9.0, and being outside a contract is not the same as being safe to leave undocumented. Because it is natively mounted, it is answered in-process and never forwarded to the worker. The agent loop reaches the same governance: an `mcp.<tool>` name a run does not govern dispatches through the very check `POST /mcp` runs. |
| Workspaces | Current | Personal workspace is default. Organization/admin surfaces remain separated from normal Brain use. Since 11.5.2, a request that names two different workspaces (header vs. query/body selector) is refused with a 403 at the chat, agent, upload, computer-use and admin surfaces — previously the header silently won at four of them. |
| VS Code Extension | Current | Sync/status endpoints expose connection and indexing state. File contents move only through explicit user actions. Recall carries the same `grounding` verdict as the web badge, staged change proposals are reviewable in place (409 conflicts reported honestly), agent runs report steps/files/outcome, and 9.9.7 adds a live `agent_step` timeline (`POST /agent` `stream:true`) plus evidence→action follow-ups from the last recall's cited sources. |
| Browser Extension | Current | Capture, recall, and approval visibility (9.9.7). Asking the Brain shows the server's own grounding verdict — an absent verdict reads "근거 확인 불가", never "근거 있음". Approval *decisions* stay off this surface by design (they need a signed short-TTL token); the extension shows that runs are waiting. Posts only to `127.0.0.1`. |
| Telegram Review | **Removed in 11.6.0** — reason stated | It was a surface of the Telegram bridge, above. The `/api/proposals` review surface it read is unchanged and native; only the Telegram rendering of it is gone. Review on web and in VS Code is unaffected. |
| Knowledge Garden | Current | `GET /api/brain/garden` answers four gardener questions from one scoped read: recent, contradictions, stale, and most-relied-on (real graph degree; Chunk nodes excluded). An unavailable graph yields empty beds, never invented ones. |
| Agent File Tasks | Current | The executor prompt carries profile-aware file-writing hints (`rust/lattice-agent/src/prompts.rs`): the order of operations, that `write_file` comes *first* on a file request, and — for the `compact` profile — the same thing as a three-step numbered list, because the weak-model failure the loop could not repair was writing nothing at all. Built-in default prompts lead with a worked example and a tool list with arg signatures; `write_file` rejects missing/empty content with a corrective error. A Self-Model summary is injected only when a caller passes one. **12.0.0 closed the copy-the-example hole**: weak models continue the nearest complete shape, and in an agent turn that shape is the prompt, so a `write_file` whose content is `prompts::WRITE_EXAMPLE_CONTENT` verbatim is recorded as `COPIED_EXAMPLE` and **written nowhere**, a guided answer opening with one of our instruction lines has that line stripped, and the critic's placeholder reason is blanked rather than shown. Each comparison is against a constant the crate owns, so none of them can reject a genuine answer. The same release stopped the RAG citation instructions leaking into the agent prompt — the model was being told to cite sources at the exact turn it needed to call a tool. |
| Agent Profiles | Current | Three dials — `standard` / `compact` / **`guided`** (12.0.0) — and the choice is **measured, not guessed**. `kernel/probe.rs` asks a newly loaded model two fixed questions once (one of them an action object whose content carries a newline and a quote, the two characters that break weak models inside a JSON string), scores the answer with **the loop's own parser**, and picks: clean → `standard`, repaired → `compact`, unparseable → `guided`. The verdict is cached per model id **and crate version**; `LATTICEAI_AGENT_PROFILE` still pins any dial and outranks everything, and `LATTICEAI_AGENT_PROBE=0` turns measuring off. The size regex survives only as the prior for when no measurement is possible, which retires the "an unlabelled small model needs a manual profile" caveat. Probing is a port (`LoopDeps::probe`), off in `LoopDeps::new` and on at the wire, so no harness or frozen trajectory silently pays for two completions. Under `compact` the loop still shortens its transcript window and escalates corrections sooner; a failed or staged write is still reported as *not* written. |
| Guided Mode (small models) | Current | The answer to "a 0.5B model cannot hold a tool-call contract at all" is to stop asking it for one. Under `guided`, a step decomposes into micro-turns: *choose an action — one number* against the run's own numbered catalog, then one turn per required argument (`path` on one line, `content` as free-form text with no JSON escaping anywhere near it). **The harness assembles the action struct** and hands it to `Runtime::perform_action` — the same tail every dial runs, so the gate chain, loop guard, pre-write snapshot and `sanitize_write_content` are identical. Verification is a closed `PASS`/`FAIL` plus one line of reason, judged by the same evidence and coverage gates a JSON verdict passes. Nothing here is a shortcut past governance, only past the JSON. **Mid-run self-demotion, downward only**: a run measured `standard`/`compact` that spends its whole format budget producing nothing demotes itself to `guided` and finishes there — guarded on three conditions (the dial was measured, it is not already `guided`, the run has no execution evidence yet) and never promoting. Demonstrated: a Qwen 0.5B reached `DONE` in 3.9s with a real file on disk. Nothing in this path names a model; per-model hacks are not allowed. |
| Uniform Tool Catalog | Current | `tools/catalog.rs` is one vocabulary for three kinds: native tools from the run's policy table, `mcp.<tool>` from the host's MCP surface, and `skill.<name>` from the skill registry — one numbered menu, one set of argument signatures, one dispatch decision. A prefixed name whose bare form the run already governs resolves to the **native** path, the stricter of the two governance chains; only a name the run has no policy for reaches the host catalog, and that dispatch runs the same governance check `POST /mcp` runs. A skill is stated to be guidance rather than an executable: choosing one returns its `SKILL.md`, which the loop keeps in front of the model, and the run still has to pick a real tool. A governance refusal, an unknown tool, or a missing tool surface is a tool error — never a bypass. |
| Folder Memory State | Current | `GET /knowledge-graph/local/health` reports per-folder indexing coverage, failures with their stored reasons, and watch state. An unscanned folder reports unknown, never "0% indexed"; vector freshness is reported once and explicitly labelled global. |
| Voice Capture | Current (transcriber optional) | `POST /api/capture/voice` ingests a memo through the unified pipeline. Transcription is an injected local port: without one the memo is still stored and reported `transcription: "unavailable"` / `searchable: false` — never an invented transcript, never a silent drop. 11.1.0 shares that one port with multi-modal ingestion, so a memo and a scanned `.m4a` can never disagree about whether this machine can hear. The memo itself is still stored through the text door (`source_type: note`) and is unaffected by `allow_multimodal`; the first-class `Audio` node type below applies to recordings the multi-modal router picks up. |
| Multi-modal Memories | Opt-in (default off) | `allow_multimodal` (`LATTICEAI_ALLOW_MULTIMODAL`) routes files by MIME/extension: images become first-class `Image` nodes (content-addressed, idempotent) with `ImageText` OCR children and chunks; recordings become first-class `Audio` nodes (`NodeType.AUDIO`) whose transcript still rides the ordinary text index — chunks, concepts, provenance and dedupe unchanged — carrying `modality`/`audio_path`/`transcription`/`searchable` metadata, and kept with an honest "nobody heard this" body when no transcriber exists. **With the flag off, behaviour is byte-identical to 11.0.x** — the folder-scan allow-list, node ids, and node types are unchanged, and both modes are asserted in `tests/unit/test_t3_ingest_routing.py`. Extraction quality scores what can actually be *retrieved* from a picture (OCR text, caption, vector). **Toggleable from the home dock's 기능 drawer** since 11.2.0 — routing changes on the next item, with no restart. |
| Vision OCR / Caption / Embedding | Current (models optional, none bundled) | OCR uses `pytesseract` when installed (absent ⇒ `ocr_status: "unavailable"`, no text). A **caption exists only when a vision-language model wrote one** — 11.1.0 removed `VisionStub`, which synthesized `Image pic.png (PNG 12x8)` from the filename and stored it where a real description would go. Image embeddings come from `VisionEmbeddingProvider` (MLX/CLIP-family or a dotted callable) and have **no hash fallback**: a hashed file path is not a picture, so an unavailable model is reported unavailable. Configure with `LATTICEAI_VISION_PROVIDER` / `_MODEL` / `_SPACE` and `LATTICEAI_VISION_CAPTION_PROVIDER` / `_MODEL`. |
| Image Retrieval | Current (behind the same flag) | Text queries find pictures through their OCR text and captions, which live in the ordinary text index. Image vectors live in a **separate** table and search (`graph/image_vectors.py`) keyed by the vision model, with mismatched widths skipped rather than truncated, and enter `hybrid_search` only by **late fusion** (`image_vector=`, `image_fusion_weight`, default 0.5) — reported in the result's `multimodal` block. `context_quality` gains a `multimodal` key only when image nodes are really in the context, so all-text answers keep the four-key shape — and both shipped producers (chat's `build_context_quality`, document generation's `retrieve_context_for_generation`) pass it, so the key appears on live searches rather than only in the retrieval layer. |
| Evidence Thumbnails | Current (minimal) | An `Image` citation in the Evidence panel shows the 96px inline `data:` thumbnail stored on the node at ingest, plus the caption when a model wrote one and an explicit "비전 모델이 없어 설명은 없습니다" line when none did. No new static route and no bypass of the `/local/serve` approval gate: only `data:image/…` is accepted, so an evidence card can never become an outbound request. Thumbnails over 24 KB are dropped rather than shipped. |
| Video Ingestion | Opt-in (needs ffmpeg) | 11.2.0 implements it, behind the same `allow_multimodal` flag (default off) plus a video sub-switch (`LATTICEAI_ALLOW_VIDEO`, on within it). Up to four keyframes are extracted with `ffmpeg` and pushed through the **existing image path** — real `Image` nodes with OCR, caption, vector and thumbnail — joined to a first-class `Video` node (`NodeType.VIDEO`) by `CONTAINS_IMAGE`; a companion `.srt`/`.vtt` with the same basename becomes ordinary text chunks. Nothing is bundled: `ffmpeg` is looked up on PATH and, absent, the ingest still answers `status: "unavailable"` — the reason changed from *scope* to *this machine*, and `multimodal_status()` names which of the three applies. A video with no subtitles is kept and says its words are not searchable rather than leaving a blank card. **Toggleable from the home dock's 기능 drawer** since 11.2.0, shown as a sub-switch of multi-modal rather than a peer. 11.5.2 had exposed the verdict over HTTP as `GET /api/ingestion/multimodal`; **11.8.0 deleted that probe** because no surface ever called it, so the reason now travels with the ingest attempt itself rather than being askable in advance. |
| Frontend Reliability | Current | Core API failures render unavailable states, successful callbacks require successful results, and Vitest/visual tests protect result, proof, conversation, primitive, i18n, and service-error behavior. **12.0.0 put an `ErrorBoundary` on every route and every heavy panel**, each with a 다시 시도 action, so a panel that throws costs its own card instead of blanking the screen. The preview pattern widened at the same time: a permission-mode change shows a diff of what it would change before it is applied, and a risky feature toggle requires an explicit acknowledgement. |
| Trusted Agent Loop | Current | LoopTrace observability + `loop` API payload, python-literal weak-model repair with escalating corrections, and proposal-first change governance (`/api/proposals`, 변경 제안 panel) where edits/deletions of existing files are reviewed before applying. **11.9.0:** `/agent/eval` is a real deterministic skill eval that reports `requires_model` honestly (the old `scripts/agent_eval.py` harness is still gone — 11.8.0 deleted it). Compact parse chain grew `tag_strip` / `balanced` / `truncated_close` / `labeled` / `fence_rescue`; the v10.8.0 salvage trio is restored; EXECUTE temperature is pinned (0.1 compact / 0.2 standard). **12.0.0:** `Completion::prefix` forces the completion to *begin* with given characters (`compact` sends `{"thoughts": "`), so a preamble, a markdown fence or a `<|channel|>` frame stops being something the model can emit rather than something the repair chain has to undo — including the one-pipe `<|channel>` variant that used to slip through into the body. A token-level JSON grammar was considered and **not** shipped: it needs a tokenizer-aware incremental parser and a per-token Python callback on the single MLX executor, and `guided` gives the same guarantee at zero per-token cost by not asking for JSON at all. A run failing the same way twice — same action, same arguments, same tool error — takes the plan-dispatch escape hatch instead of spending its whole step budget on a refusal it has already had. Small-model *content* quality is still gated — see Known Limitations. |
| Command Center | Current | `/api/command/briefing` + `/api/command/search` aggregate knowledge, conversations, automations, review, health, and suggestions read-only and workspace-scoped; surfaced as the Cmd+K palette and Today's Briefing panel. **11.7.0:** the knowledge group reads `matches` (11.6.0 ported the oracle's empty-`results` bug and Cmd+K could never surface a node). Briefing freshness is frozen at capture in replay so the 45-day fuse cannot redden the fixture on 2026-09-28. |
| Evidence → Action | Current | `POST /api/evidence/actions` composes evidence-scoped follow-up prompts (요약/체크리스트/문서/한 페이지) from an answer's real citations; deterministic and model-free, executed through the normal chat path. Unresolvable citations are reported, never dropped. |
| Run Explanation | Current | Every agent run returns a deterministic `explanation` (why it ended, how much the model struggled, one concrete next step). It never upgrades a non-success; `ok` is true only for a verified `DONE`. Rendered on web and in VS Code (the Telegram surface was removed in 11.6.0 with the bridge). |
| Project Sessions | Current | `/api/projects` keeps a project's produced files, open TODOs, and last honest verification across runs; `/agent` accepts `project_id` and folds each run's outcome (including the last failure's diagnosis) back in. |
| Citation Precision | Current | A sentence-aware `prose` chunking strategy keeps Korean claims whole for `.txt/.pdf/.docx/.html`; chunk hits carry a locator (`Guide > Setup · p.4`) and stay silent when they cannot prove it. `plain` chunking is byte-identical to the legacy walk. |
| Graph Relation Evidence | Current | Relations record whether they came from a verb or from co-occurrence, with matching weights; enumerations no longer manufacture relation chains, and the curator can demote weak/hub adjacency edges without touching verb-backed or legacy ones. |
| Funnel Alerts | Current | `GET /api/admin/funnel-metrics` returns named, actionable alerts with the value that triggered them; rules stay silent below 10 samples. |
| Frontend Payload | Current | Every route is a `React.lazy` boundary and copy follows the route: `shell` copy registers eagerly, `brain` / `workspace` / `onboarding` register inside the lazy chunk that needs them. Initial static JS is **104.2 KiB** gzip against a 150 KiB budget, as `npm run check:bundle` measures it (the figure had read "~99 KiB" since 9.9.9 without being re-measured). 12.0.0 split the Act and Brain sub-routes into their own lazy chunks, so the added `ErrorBoundary` coverage costs the first paint almost nothing. `npm run check:i18n-namespaces` walks the real module graph and fails the build when a chunk reads a key it never imported — the failure mode is otherwise silent, because `t()` returns the raw key and the UI renders an identifier instead of text. |
| Permission Modes | Current | `strict` (default) / `trusted` / `bypass` over the existing ToolRegistry + Change Governor, resolved per user and per workspace and stamped once per agent run (a paused approval resumes under the mode it was approved with). Circuit breakers are mode-invariant: destructive risk, root/home paths, `rm -rf /` style commands, and binary overwrites are denied in every mode. Set it in **설정 → 혼자 해도 되는 일** (`SystemPage` settings tab), on the home screen dial, or through `POST /api/permission-mode`. The reader sees 먼저 물어보기 / 웬만하면 알아서 / 거의 다 알아서 — the wire tokens are unchanged. The selector still renders the server's own catalog rather than a hardcoded mode list (`frontend/src/lib/permissionCopy.ts` supplies the plain wording by mode id and falls back to the server's label for an id it does not know), and refuses to send a `bypass` switch until the risk acknowledgement the server requires is ticked. |
| Network Boundary | Current | `NetworkBoundaryMode` (`local_only` default / `cloud_allowed`) decides whether any knowledge may leave the host, orthogonal to PermissionMode. Set it in **설정 → 내 지식이 나가는 범위** (`NetworkBoundaryPanel`) or through `POST /api/network-boundary`. The selector renders the server's own catalog and refuses to send a `cloud_allowed` switch until the risk acknowledgement the server requires is ticked. A built-in **preview** names the actual memories a given question would send, with its token estimate and whether the token guard would refuse the turn — and works in `local_only` too, labelled as hypothetical. Only the minimal extracted node slice is ever sent, never the graph. Nodes flagged `sensitive` / `private` / `do_not_share` / `local_only` are filtered in **both** modes (mode-invariant, like the agent circuit breakers). |
| Hybrid Cloud Chat | Current (dual credentials) | When the boundary is `cloud_allowed`, `/chat` may escalate to a cloud provider. Credentials resolve from `<data_dir>/cloud_provider.json`, then `LATTICEAI_CLOUD_API_KEY` (+ optional base URL / model), then a locally OAuth-authenticated CLI (`agy` → gemini-3.7-flash, else `grok` → grok-4.6). `api_key` mode streams an OpenAI-compatible HTTP adapter (the cloud model comes from the provider config, never the local MLX id) — **mock-server verified only; never live-billed**. `cli_oauth` mode spawns the CLI in a fresh temp dir with a 120s timeout; live OAuth E2E ran at zero API billing. Escalation is `hybrid_policy.json` `escalation`: `always` / `auto` (default) / `manual`. `auto` sends only when no local model is loaded, local context is thin (< 2 matched nodes), or the user prefixes `/cloud ` / `클라우드:`. `network_mode: local_only` on the request always wins. `GET /api/cloud/status` answers `{configured, mode, provider, model, detail}`. Token frames stream as they arrive; `stream: false` is a JSON body. SPA shows a ☁️ chip (provider/model, memories sent, pending knowledge proposals), a composer boundary hint, a 「이번 대화는 로컬만」 toggle, and a System-panel provider row. Inert when nothing is configured; the local path is untouched. |
| Cloud Memory Write-Back | Current (proposal-first) | Knowledge extracted from a cloud answer is enqueued as a Review Center `kg_cloud_expansion` `change_proposal` with provenance. The host binds the live review queue and the egress audit sink, so a cloud turn stages a proposal and writes a shape-only `cloud_egress` record (provider / model / reason, never content). It is written to the graph only when `auto_commit` is explicitly enabled in the hybrid policy (default **false**); an unreadable policy is treated as the default, never as permission. Multimodal streaming needs both `cloud_allowed` and a separate `allow_multimodal` flag (default **false**). |
| Obsidian Vault Bridge | Current (manual sync + opt-in watch) | `POST /api/ingestion/obsidian` reads an *external* Obsidian vault the user approves through the standard local-read approval dance and pushes every `.md` note through the one native ingest gate (`source_type: obsidian`). In-vault `[[wikilinks]]`, `![[embeds]]`, and relative markdown links become `REFERENCES` edges between the note nodes; frontmatter `tags` become workspace-scoped `Topic` nodes joined by `TAGGED_AS`. A link whose target is missing or ambiguous is reported in `links.unresolved`, never guessed. Re-running is idempotent (content-hash dedup plus deterministic edge/topic ids). `dry_run` reports note/link/tag counts without writing. Distinct from the `obsidian_save`/`obsidian_search` tools, which write Lattice's own mirror vault. |
| Interop Bridges | Current (local files, opt-in) | `GET/POST /api/ingestion/interop` reads a **Notion export** (directory or `.zip`; the 32-hex id is split off the title and kept in metadata, page links become `REFERENCES` edges), a **local Git repository** (`git log` → one node per commit, changed file paths joined as `Topic`s, idempotent on the commit hash), and local **`.eml` / `.ics`** files (stdlib `email`; a five-rule VEVENT parser, no new dependency). Every item goes through the one native ingest door with the same local-read approval dance as the vault bridge, and `dry_run` reports what a real run would touch. System integration — IMAP, the Notion API, a macOS Calendar/Mail grant — stays out of scope and says so; `git` must be on PATH, which the status route reports rather than discovering on use. |
| Recipient-Key Sharing | Opt-in prototype (off by default) | A shared subgraph can be sealed to the receiver's **X25519 public key** (HKDF-SHA256 → AES-256-GCM, a fresh ephemeral keypair per bundle, so a later key compromise does not open an old one) instead of a shared passphrase — nothing secret has to travel first. `GET /api/knowledge-graph/share/recipient-key` publishes the receiving key; the sender passes it to `POST /api/knowledge-graph/share/archive`. Choosing both mechanisms, or neither, is refused rather than guessed. The Ed25519 **signature is unchanged**: signing says who wrote a bundle, sealing says who may read it. |
| Bulk Review Actions | Current | `POST /automation/reviews/bulk/{approve,dismiss}` decide up to 200 named items through the *same* single-item guards — an already-decided item still conflicts, a `change_proposal` still applies its staged content, and the audit trail records N decisions. The response carries a per-item verdict (`ok` / `not_found` / `conflict` / `failed`), so a partial success is legible instead of a single number. `ids` is required; there is no "approve everything pending". |
| Selective Brain Network | Opt-in prototype (off by default) | `GET/POST /api/knowledge-graph/share*` export a *chosen* subgraph — node ids, node types, or source types, optionally one hop out — as a bundle signed by this device's Ed25519 identity, with a payload digest pinned inside the signed header. The receiving Brain verifies fail-closed and files every node as a **review proposal** carrying the sender's fingerprint; the graph changes only when a person accepts one item, and an edge into a node the receiver does not have is deferred and reported rather than written dangling. Everything is behind `LATTICEAI_BRAIN_NETWORK` (default off); while off the mutating routes answer 403 with the reason and `GET /api/knowledge-graph/share` still answers `enabled: false`. **Toggleable from the home dock's 기능 drawer** since 11.2.0, where it is the one switch that carries a caution line — it is the only one that sends knowledge off this machine. |
| SSO / OIDC login | **Removed in 11.6.0** — reason stated | The worker mounts no `/auth/*` at all, so the OIDC login and callback flows went with `authlib` and `cryptography`. **The configuration surface remains** (the settings are still read and still shown), and **password login is native** in `lattice-auth` — sessions, roles, rate limits and CSRF included. Restoring the flows means porting them to `lattice-auth`, which is a decision this release did not take rather than one it hid. |
| Release Assets | Current | 12.0.0 package metadata, static app, release notes, current documentation, and exact artifact names are aligned. The macOS dmg is ad-hoc signed (effectively unsigned), as in every release so far. |

## Known Limitations

- **Small-model *content* quality is honestly gated.** The mechanical half is
  solved: with `guided`, a model as small as 0.5B writes the requested file
  and reaches `DONE` (3.9s in the release run), and the harness never asks it
  for JSON. The *writing* is the part that still fails — a weak summary is
  caught by the critic and the run ends `FAILED` / `NEEDS_REVIEW` rather than
  claiming success. The funnel still advises a larger model for agent runs.
  The cross-model matrix is a release-time measurement; the honest current
  reading is "improving from 11 of 18", with the remaining failures of this
  same content-quality kind.
- **The `api_key` cloud path is mock-verified only.** It is contract-tested
  against a mock server and was never live-called — there is no billing
  budget for that path, and that is a policy, not an oversight. Live OAuth
  E2E used `cli_oauth` (`agy` / `grok`) at zero API billing.
- **The macOS dmg remains ad-hoc signed** (effectively unsigned). First
  launch needs the usual Gatekeeper step. `npm run release:validate` checks
  names and presence, not a Developer ID.
- **Vector search still defaults to `brute`.** `hnsw+rescore` is real and
  used when opted into (`LATTICEAI_VECTOR_INDEX=hnsw`), but the default is the
  exact scan: it is exact, byte-compatible with every previous release, and
  needs nothing installed. An opted-in `hnsw` that cannot answer falls back to
  brute carrying its reason rather than returning a short list.
- **Watch never deletes.** A file that disappeared from a watched folder is
  reported, not removed. Cleanup happens only through the explicit
  `POST /api/ingestion/folder/prune` consent flow (dry-run, then `confirm`).
- **The 12.0.0 crate regrouping is a move, not a decoupling.** The domain
  lines are drawn and stated, but the four couplings that cross a domain line
  in `lattice-platform` are named in the relevant `mod.rs` rather than
  removed, and `lattice-agent`'s compatibility map exists precisely because
  four other crates import its old paths.
- **`GraphWriter::delete_node` still leaves `PART_OF` behind.** The prune door
  does not use it — `delete_document_tree` clears both directions — but the
  primitive itself is unchanged, so a caller that reaches for the simpler
  function inherits the same dangling edge Python had.
- **Some API refusals are still English literals, and the inventory moved.**
  The "33 of 50 routers" figure this list carried was a 10.9.0 count of
  *Python* routers; since 11.6.0 the product routers are Rust and that count no
  longer describes the tree. What the live ratchet
  (`scripts/check_server_i18n.mjs`) actually locks today is **4 worker routers**,
  and it reports **2** as unclaimed (`health`, `search`). The larger leftover is
  native Rust `detail` strings that never entered `latticeai/core/messages.py`
  at all — closing that needs a Rust-side catalog plus a ratchet over
  `lattice-platform` / `lattice-auth` refusals. The everyday SPA path is
  Korean; this is the remaining API/error English.
- **A long download no longer freezes the server, but it is still not
  cancellable.** Closing the tab mid-pull leaves the pull running to completion
  on its worker thread.
- **The background embed queue now has a driver, with limits worth naming.**
  11.5.0 closed the gap this list carried since 11.1.0. The server exposes
  `POST /api/index/drain` (`require_user` plus the workspace gate; `limit`
  1-100, default 25) and `GET /api/index/queue`, and the Rust scheduler in
  `lattice-jobs` calls the drain every 60s by default
  (`LATTICEAI_JOBS_INTERVAL`, floored at 5s, backing off to 10 minutes after
  consecutive failures and snapping back on the first success). The schedule is
  visible and forceable through the host's `host/jobs` and `host/jobs/tick`
  routes. What that does *not* mean: the backlog is machine-wide (one SQLite
  queue serves every workspace, so a drain embeds whatever is owed and the
  counts are totals for this Brain); the timer only exists while a host process
  is running it, so a worker started on its own still has the endpoint and no
  clock; resuming interrupted ingestion jobs is opt-in
  (`LATTICEAI_JOBS_AUTORESUME=1`) and takes at most one partial-or-failed job
  with work left per tick; and on an install with authentication switched on an
  unauthenticated tick answers 401, which surfaces verbatim in the schedule's
  `last_tick.error` rather than passing as a quiet success.
- **The HNSW index appends now, and is still not the default.** Through 11.9.0
  the `.hnsw` sidecar was fingerprinted on row count plus newest `indexed_at`,
  so *any* write invalidated it and the next search paid a full rebuild
  (measured in `docs/PERFORMANCE.md` as the "first query" column). 12.0.0
  appends new vectors into the existing index and uses it in real search
  (`hnsw+rescore`: worker-sidecar candidates, native exact rescore). It stays
  **opt-in**: `brute` remains the default until recall@10 against the exact
  scan is re-measured on a Brain that has been ingesting continuously.
- **The quantized backend's memory advantage is not realized in this release.**
  int8 codes are 8x smaller than boxed floats, but the measurement says peak
  memory barely moves (38.4 MB vs 39.7 MB at 10k): the exact scan already feeds
  the index in bounded batches, so resident vectors were never the dominant
  term — the fetched SQLite rows are. What the measurement does show is ~2.2x
  the latency of the exact scan (641 ms vs 293 ms at 10k) for 0.987 recall. It is
  shipped as a working, exhaustive backend and as the representation a held
  index would need; on today's numbers there is no reason to prefer it.
- **Video ingestion needs `ffmpeg` on the machine; nothing is bundled.** 11.2.0
  implements it — up to four keyframes become ordinary `Image` nodes joined by
  `CONTAINS_IMAGE`, and a companion `.srt`/`.vtt` becomes ordinary text chunks
  — so the refusal that remains is a *runtime* one, not a scope one: with no
  decoder the ingest still answers `status: "unavailable"` and says which of
  the three reasons applies (multi-modal off, video sub-switch off, no ffmpeg).
  A video with no subtitles is stored and says plainly that its words are not
  searchable. It sits behind `allow_multimodal`, whose default is unchanged.
- **No vision or speech model ships with the product.** OCR needs a local
  `pytesseract` + tesseract install; captions need a loaded VLM
  (`pip install "ltcai[local]"` plus `LATTICEAI_VISION_CAPTION_*`); image
  embeddings need `mlx_clip` or a dotted callable; transcription needs a
  transcriber port that the default build does not wire. With none of them the
  feature still works and still says what it could not do — an image is stored,
  findable by filename, and its metadata reads `ocr_status: "unavailable"` /
  no caption / `vision_embedding: "unavailable"`. Nothing is fabricated to fill
  those fields.
- **Image evidence is shown from a 96px thumbnail, not the original file.**
  Serving the real image would need either a new static route over the user's
  disk or a reuse of `/local/serve`, which exists so every read passes an
  explicit approval. The inline `data:` URI avoids both and is capped at 24 KB,
  so a noisy or very large picture falls back to a labelled badge plus the
  filename rather than shipping a heavier payload inside every recall.
- **Text queries reach the image space only with a shared-space model.** A
  typed question still finds pictures through their OCR text and captions.
  11.2.0 adds an opt-in `image_fusion` parameter to `/api/search/hybrid` that
  vectorizes the query itself — but only when a genuinely shared-space vision
  model is configured (`LATTICEAI_VISION_SPACE=shared`, and only the `mlx`
  provider has a text tower). Without one the response says so
  (`multimodal.image_fusion.detail`) instead of returning the text-only ranking
  as if fusion had happened, and `GET /api/search/image-query` reports the same
  verdict up front. The switch itself is off by default
  (`LATTICEAI_TEXT_IMAGE_FUSION`, or the home dock's 기능
  drawer).
- **Interop bridges read exported files, never a vendor API.** 11.2.0 adds
  Notion (an export directory or `.zip`), a local Git repository's history, and
  local `.eml` / `.ics` files, all through the same native ingest door as
  the vault bridge. What stays out of scope is **system integration**: no IMAP,
  no Notion API, no macOS Calendar/Mail permission grant. Those need
  credentials and a background sync, and "point me at what you exported" is the
  honest version. Git additionally needs `git` on PATH — `GET
  /api/ingestion/interop` reports which bridges this machine can actually run.
- **A vault watch now skips unchanged notes, but still runs one bridge pass.**
  12.0.0 put the ingest fingerprint on the watch path, so a note whose size,
  mtime and content hash are unchanged is not re-read, re-chunked or
  re-embedded. What has not changed is the shape of the pass itself — the
  11.1.0 objection still stands, because link edges need node ids only a
  completed ingest has. It is opt-in and off by default
  (`LATTICEAI_VAULT_WATCH`, the injectable gate, or the home dock's 기능
  drawer). A large vault is still capped at 2,000 notes per run and reports
  `truncated: true`; only frontmatter `tags` become topics, inline `#tags` are
  not parsed. Watch still never deletes: a vanished note is reported, and the
  prune door removes it only on confirmation.
- **Subgraph sharing can now be sealed to a recipient's public key.** The
  bundle is still signed by the sending device (Ed25519) — signing says who
  wrote it, sealing says who may read it — and encryption is either the 11.1.0
  passphrase (PBKDF2 + AES-GCM) or an X25519 sealed box (HKDF-SHA256 +
  AES-256-GCM, ephemeral key per bundle). `GET /api/knowledge-graph/share`
  reports `recipient_public_key_encryption: true` and both modes;
  `GET /api/knowledge-graph/share/recipient-key` publishes the receiving key.
  Bulk accept exists for the review inbox
  (`POST /automation/reviews/bulk/{approve,dismiss}`, per-item verdicts), but
  the receiving UI is still the existing Review Center rather than a dedicated
  share screen, and sharing remains a prototype behind `LATTICEAI_BRAIN_NETWORK`.
- **The Self-Model has no screen yet.** Everything is reachable through
  `/api/memory/self-model*` and the Review Center renders its proposals like any
  other, but the `/app` UI has no dedicated profile view, so "see everything the
  Brain thinks about me" is an API call today.
- **Self-Model extraction is a phrase table, not comprehension.** It matches
  first-person Korean and English patterns (`저는 …를 선호합니다`, `결정:`,
  `매일 …`, `I prefer …`, `my colleague …`). A fact stated any other way is
  missed, and a matched phrase can over-capture the rest of its clause — which is
  survivable precisely because every candidate is a proposal a person reads before
  it is stored. The optional `refiner` hook can hand a model the wording, but no
  model is wired to it in this release.
- **The Self-Model summary now reaches the agent loop too.** `AgentDeps.
  self_model_summary` (11.2.0) takes a fixed string or a scoped resolver, and
  `_executor_context` resolves it **once per run** and hands it to
  `executor_prompt_for`. An empty or unreadable profile injects nothing, so the
  prompt bytes are unchanged for a Brain that knows nothing about its owner.
  What is still missing is a screen: the profile is API-only (below).
- **Reorganization proposes one level of topic folders and cannot be undone in
  one click.** Targets are always `topics/<주제>/<file>`; nested schemes, renames,
  and merges are out of scope. Applying leaves no reverse proposal — the moved
  files are still in the workspace, but putting them back is a new proposal. Only
  files the graph links to a topic move at all, so a Brain that has not indexed a
  folder proposes nothing for it.
- **The browser extension resolves its language from the browser**, not from the
  web app's setting. The popup is a separate origin and cannot read
  `lattice.language`; closing this needs a server-side preference, which does
  not exist yet.
- The plain-mode vocabulary sweep in `tests/visual/v3.shell.spec.js` checks a word
  list over ten routes. It catches the engine's vocabulary reaching a reader;
  it cannot catch a sentence that is jargon-free and still unclear.
- SQLite is the live local Brain store. PostgreSQL/pgvector remains optional
  scale/migration tooling and requires explicit setup.
- Package registry publishing is owner-run and can lag behind the GitHub
  release.
- Docker setup, model downloads, cloud model calls, Brain Network, update
  checks, and marketplace refreshes are explicit opt-in paths.
- **The Telegram bridge and the SSO/OIDC login+callback flows were removed in
  11.6.0**, both as consequences of the worker boundary. The SSO configuration
  surface remains and password login is native.
- **11.7.0 closed the three leftover seam leaks and the three ported oracle
  bugs** that 11.6.0 listed here. Command-search knowledge returns results;
  snooze accepts offset-aware datetimes (invalid `until` → 422); double-reject
  is 409; binary upload parse, per-chunk vectors, native hooks, sanitize on
  write, review timeline events, and a single `workspace_os.json` writer all
  shipped. A static gate keeps a new stranded `/worker/` path from landing.
- **`POST /worker/render/pdf` ships with `reportlab` as a required dependency**
  (since 11.6.0). `ltcai[pdf]` remains an empty alias for older install lines.
- **The six pyautogui pointer tools execute in the worker**, not natively. On a
  stock install they answer "unavailable" exactly as before; a user who installed
  `pyautogui` into the worker venv keeps working pointer control. Closing this
  needs a native actuator — a decision, not an oversight.
- **11.8.0 traded two enforced claims for smaller ones**, and neither is an
  improvement dressed up as one. The Python coverage gate went from
  `fail_under = 100` on lines *and* branches to **line coverage with a floor of
  90**, and branch measurement is off; the measured figure for this release is
  still 100%, but a later commit at 92% now passes. The local `npm run lint`
  chain went from thirteen gates to **ten** — the three that left had either
  lost their script (`check_legacy_debt.mjs`), moved to a direct CI invocation
  (the extension tests), or were already covered by another member.
- **`.github/workflows/agent-smoke.yml` was deleted with nothing in its place.**
  Hosted runners carry no MLX model, so the job failed open on the missing model
  and then reported that fail-open as a pass — two layers of "we could not
  check" rendered as green. Restoring the check needs a real-model runner, which
  is a decision, not an oversight.
- **The image and video multimodal functions have no HTTP door** as of 11.8.0.
  `POST /worker/multimodal/describe` was the only one, and it wrapped a native
  image ingest that was never built. The observation functions stay in Brain
  Core under direct unit test with the reason in the module header; the audio
  half still has `POST /worker/asr`.
- **Message-catalog keys for the nine deleted routes remain in the frozen
  parity fixture**, deliberately. That fixture records the surface *as it was
  captured*; rewriting it to match today's tree would destroy the thing it
  exists to prove.
- **`tests/visual/mock_server` still serves one orphan mock route.** The current
  screenshot evidence is hash-bound to that server, so removing the route now
  would break the evidence binding. It goes in the next capture cycle.
- **Named leftovers from the 11.7.0 sweep** (not silently dropped): Self-Model
  `open_keys` accepts `pending` only (Python also accepted `snoozed`); there is
  still no wording `refiner`; review timeline events are silent in a standalone
  retrieval process with no owner installed; `POST /knowledge-graph/ingest` is
  text-only by contract; every review mutation is two store cycles; the
  snooze-422 detail is raw English like its sibling refusals. (`delete_node`'s
  dangling `PART_OF` is listed above, with what the prune door uses instead.)
- Agent/workflow simulation without a loaded LLM is deterministic and must stay
  labeled as model-free rather than autonomous model execution.
- Local file privacy depends on the user's OS account, disk encryption, and
  backup policy outside Lattice AI.
- Surface parity has no recorded gaps as of 9.9.7; every remaining "—" in
  `docs/SURFACE_PARITY.md` is a design boundary that states its reason (e.g.
  approval *decisions* stay off the browser extension because they need a
  signed, single-use, short-TTL token).
- Voice transcription ships with **no bundled transcriber**. Memos are stored
  and honestly marked not-searchable until a local transcriber is wired in. The
  status probe that reported which case applied (`GET /api/capture/voice/status`)
  was **deleted in 11.8.0** — no surface read it — so the verdict now arrives on
  the memo itself (`transcription: "unavailable"` / `searchable: false`).
- The agent profile is **measured** since 12.0.0, not read off the model id: a
  two-question probe scored by the loop's own parser picks `standard` /
  `compact` / `guided`, cached per model id and crate version. The size regex
  survives only as the prior for when no measurement is possible, so an
  unlabelled small model no longer needs a manual `LATTICEAI_AGENT_PROFILE`.
  The env pin still outranks everything, and `LATTICEAI_AGENT_PROBE=0` turns
  measuring off. What a probe cannot tell you is how the model will do on a
  *hard* task — which is why a measured run that produces nothing demotes
  itself to `guided` mid-run.
- The conversation artifact ledger is process-local and bounded — it answers
  "what did this conversation just make?" for minutes, not days. After a
  restart, normal retrieval covers it because indexing has caught up.
- Requirement coverage blocks completion only for *declared* project files;
  matching a prose feature request to a transcript stays the critic's
  judgement and is advisory.
- The last root compatibility module is gone: `server.py` left with `create_app`
  in 11.6.0, so `uvicorn server:app` has nothing to serve. The worker starts as
  `python -m uvicorn latticeai.worker_app:create_worker_app --factory`; the
  product starts as the `lattice-host` binary. A Python test still blocks
  reintroduction; 11.8.0 removed the mjs mirror of that rule
  (`scripts/check_legacy_debt.mjs`), which had already drifted from it.

## Release-Era History Kept In Git

The Git tree keeps supported release history from:

- 12.0.0
- 11.9.0
- 11.8.0
- 11.7.0
- 11.6.0
- 11.5.2
- 11.5.1
- 11.5.0
- 11.4.0
- 11.3.0
- 11.2.0
- 11.1.0
- 11.0.1
- 11.0.0

11.6.0 rebuilt the product server in Rust, so a 10.x or 9.x install is a
different program and `SECURITY.md` supports only 11.x. Their note files remain
in the tree as history rather than as supported releases; anything older than
8.0.0 was removed from the tracked tree.
