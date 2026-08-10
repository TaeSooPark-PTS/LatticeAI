# Lattice AI Feature Status (v11.1.0)

> **Status: canonical** — current-truth feature state, kept in sync with the
> current release.

Current release: **11.1.0 — Product Intelligence**.

This file describes the current product state and known limitations. Historical
change history is intentionally limited to 9.0.0 and later in `RELEASE.md` and
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

## Current Feature Status

| Area | Status | Notes |
| --- | --- | --- |
| Brain Home | Current | Living Brain, composer, and Brain Brief are visible in the first viewport on desktop and mobile. |
| Automation Intelligence | Current | /api/automation mines recurring user questions (deterministic local clustering, literal-question evidence) and connected knowledge folders into one-click suggestions; installs are idempotent, disabled-draft, review-queue-gated workflows. |
| Brain Intelligence | Current | The Brain diagnoses itself: /api/brain health scoring (freshness, connectivity, search readiness, consistency), proactive insights digest, contradiction surfacing, and consent-first duplicate consolidation, wired from the lattice_brain quality layer and covered by unit + live-boot tests. |
| Temporal Knowledge | Current | Nodes and edges carry `valid_from` / `valid_to` / `superseded_by`, added by an idempotent additive migration on an existing Brain (NULL means "since `created_at`" / "still true" — never an empty string, and no backfill). `store.as_of(timestamp)` returns the graph slice that was valid at that instant, and `neighbors(..., as_of=…)` takes the same slice; both default to today's behaviour when the argument is omitted. |
| Proactive Synthesis | Current | The Brain notices on its own and asks rather than acts: contradicting memories, recurring-but-unnamed topics, always-together-never-linked pairs, and decayed episodic fragments all arrive as Review Center proposals (`kg_change_digest`) with a plain-language explanation. Deterministic — token overlap and clock arithmetic, no model needed; a model may only reword the weekly brief. Runs event-driven: the ingestion pipeline's audit seam hands every landed ingest to `BrainIntelligenceService.note_ingest`, and a pass fires every `LATTICEAI_SYNTHESIS_THRESHOLD` (25) genuinely new nodes — duplicates do not count, and a trigger that fails never fails the ingest. **Every write goes through approval**: `POST /api/brain/contradictions/resolve` approves the proposal first and only then stamps the pair's validity windows (keep / replace / keep both with time ranges), and the same pair is never proposed twice while it is still waiting. |
| Memory Decay | Current | `GET /api/brain/importance` scores each memory by use (ingested access counts, else the store's own read counter) plus recency decay, and names the weakest *episodic* fragments only — a decayed Decision or Document is reported as stale knowledge, never folded away. `/api/brain/quality-report` carries the same numbers plus a `tidying` flag so "the Brain is tidying up" is visible rather than a background surprise. |
| Hybrid Recall | Current | /api/memory/recall and the graph-layer `hybrid_search` blend lexical evidence with vector similarity (hybrid-evidence/v2 gate) with workspace-scoped vector hits and honest lexical fallback when the vector tier fails. Chat consumes a `context_quality` signal so grounding reflects how strong the retrieved context actually is. |
| Folder Ingestion | Current | `ingest_folder` indexes a chosen local folder with `.latticeignore` filtering; long runs execute as resumable background jobs surfaced through `/api/ingestion/jobs` rather than a single blocking request. |
| Extraction Quality | Current | Ingestion scores per-source `extraction_quality` and runs an observe-mode `quality_gate` that flags low-quality extractions instead of silently accepting them. |
| Vector Freshness | Current | `/api/brain/vector-freshness` reports embedded-vs-total content so stale embeddings are visible and reindexing can be triggered on demand. The store adds `vector_freshness_breakdown()` — embedded / missing / stale / queued — because "12 pending" hides the difference between twelve never-embedded imports and twelve edits whose current answers are quietly wrong. |
| Vector Index Backends | Current | `LATTICEAI_VECTOR_INDEX` selects `brute` (default, exact, byte-compatible with every previous release), `quantized` (int8 storage, exhaustive, approximate scores) or `hnsw` (approximate nearest neighbour, needs `pip install "ltcai[hnsw]"`). An unknown name or a missing extra falls back to the exact scan **and says why** — in `index_status().storage.vector_index` and in each search result's `index` block. Approximate backends set `approx: true`, which reaches `context_quality`. Measured at 10k and 50k vectors in `docs/PERFORMANCE.md` with `scripts/bench_vector_index.py`, which reports recall@10 against the exact scan next to every latency number: hybrid p50 **10.1 ms at 10k / 43.9 ms at 50k** with HNSW (target was < 50 ms at 10k) against 299 ms / 1515 ms for the exact scan, at 0.95–0.99 recall. |
| Background Embedding | Current | A failed inline vector sync now queues durable work (`vector_jobs` in the brain database) instead of leaving `indexing_status: "pending"` for a human to notice. `IngestionPipeline.drain_vector_queue()` runs one tick, retries are bounded, and a node that exhausts its budget stays visibly `failed` with its last reason. Caller-driven by design: the queue survives restarts, so *who* runs the worker is a deployment decision, not a side effect of ingesting. |
| Fusion Strategy | Opt-in (default off) | `LATTICEAI_FUSION_STRATEGY=rrf` fuses channel *positions* (Reciprocal Rank Fusion) instead of the lexical channel's `1/rank` and the vector channel's normalized cosine, which are not on comparable scales. Per query class via a JSON object. Off everywhere by default: alpha fusion is the ranking this release's assertions describe. |
| Graph Candidate Expansion | Opt-in (default off) | `LATTICEAI_GRAPH_EXPANSION=1` adds the one-hop neighbours of the strongest hits to the candidate pool, so a node one edge away from the match is reachable at all. Capped at 5 candidates from 3 seeds, scored at half its seed's score, and reported in the result's `graph_expansion` block (seeds walked, candidates added, whether the cap bit, seeds that failed). |
| Self-Model (Personal Ontology) | Current (API only) | The Brain keeps a small, separately-governed subgraph about *its owner*: `Self` / `Preference` / `Habit` / `Relationship` node types (plus the existing `Decision`), rooted at `self:root` with `PART_OF` edges. Extraction is deterministic — first-person Korean and English phrasings, no model required — and **only ever proposes**: `POST /api/memory/self-model/propose` files each candidate in the Review Center, and `POST /api/memory/self-model/apply` writes it *after* the approval returns. The user owns it: `GET /api/memory/self-model` shows every fact and the exact summary that gets injected, `POST` corrects one, `DELETE /api/memory/self-model/{id}` forgets it — both direct, because it is their own profile. A summary rides along with document-generation context (empty profile → nothing injected, never more than half the context budget). |
| Workspace Reorganization | Current (proposal-first) | "이 프로젝트를 정리해줘" produces a plan, not a tidy-up: `WorkspaceOSStore.propose_reorganization` asks the graph what each file is about and stages **one** `change_proposal` (kind `folder_reorganization`) describing every move into `topics/<주제>/`. Files the Brain cannot justify are reported as `unplaced` with a reason instead of being swept somewhere plausible, and **no deletion is ever proposed** — the planner has no delete path at all. Approving from the Review Center applies it through the same `ChangeProposalService` door every other staged change uses; a move whose source vanished or whose target now exists is skipped and reported, never forced. |
| Change Governance | Current | `core/tool_governor.py` `MUTATING_TOOL_INVENTORY` requires every mutating tool to be governed or explicitly exempt (release-checked). File edits/deletions flow through change proposals that record a base content hash and re-check it for conflicts before applying atomically. `core/agent_eval.py` verifier fails closed to `NEEDS_REVIEW` on unverifiable or failing outcomes. |
| Brain Brief | Current | MemoryService turns real workspace, conversation, graph, vector, and source-health signals into focus, evidence, and next actions. `GET /api/brain/proactive-brief` adds the proactive section — what the Brain noticed and what is waiting for a decision — read-only, counting the proposals already in the Review Center rather than raising new ones. |
| Conversation | Current | Chat is the primary action. It refuses to fake model output when no model is loaded, surfaces memory proof when context exists, and routes explicit file actions into the governed workspace file tool. |
| Knowledge Graph | Current | Memory graph exploration, graph read compatibility, provenance-aware retrieval, fail-closed workspace reads/traversal, explicit legacy-global compatibility, workspace-safe duplicate content, and KG v2 equivalence gates remain active. |
| Source Capture | Current | Files, folders, notes, and web/source capture paths feed Brain memory and graph context through explicit user actions and the unified ingestion pipeline when available. A provenance record is keyed by its origin (node, content hash, source type, source URI, pipeline), so re-scanning an unchanged folder or vault updates one record per source instead of appending a duplicate — through 11.0.x the key included a wall-clock second, which made that behaviour depend on how fast the machine was. |
| Local Models | Current | Setup and model recommendation flow remains explicit; model downloads and runtime installs require user action. |
| Installer Audit | Current | Setup Wizard, auto setup, and engine installers expose redacted command plans, require confirmation tokens, and write local process audit events. |
| Cloud Models | Opt-in | Cloud prompts are sent only after keys are configured and the user selects a cloud model path. |
| Telegram | Opt-in / fail-closed | The bridge starts only with a bot token, explicit chat-ID allowlist, and dedicated server session bearer; unauthorized messages and callbacks are rejected before registration. |
| Agent Runtime | Current | AgentRuntime preview/readiness contracts avoid tool execution during preview, reject unknown roles, require explicit human approval for non-auto-approved plans, tolerate legacy run events with contract envelopes, and expose orchestration boundaries. |
| Tool Registry / MCP | Current | ToolRegistry diagnostics, explicit desktop/knowledge/network policy gates, masked MCP paths, and MCP install state are separated from app-factory helpers and covered by focused tests. |
| Workspaces | Current | Personal workspace is default. Organization/admin surfaces remain separated from normal Brain use. |
| VS Code Extension | Current | Sync/status endpoints expose connection and indexing state. File contents move only through explicit user actions. Recall carries the same `grounding` verdict as the web badge, staged change proposals are reviewable in place (409 conflicts reported honestly), agent runs report steps/files/outcome, and 9.9.7 adds a live `agent_step` timeline (`POST /agent` `stream:true`) plus evidence→action follow-ups from the last recall's cited sources. |
| Browser Extension | Current | Capture, recall, and approval visibility (9.9.7). Asking the Brain shows the server's own grounding verdict — an absent verdict reads "근거 확인 불가", never "근거 있음". Approval *decisions* stay off this surface by design (they need a signed short-TTL token); the extension shows that runs are waiting. Posts only to `127.0.0.1`. |
| Telegram Review | Current | `/review` lists staged change proposals from the same `/api/proposals` surface with inline approve/reject; a 409 reports that nothing was written. Answers carry the server's grounding verdict. |
| Knowledge Garden | Current | `GET /api/brain/garden` answers four gardener questions from one scoped read: recent, contradictions, stale, and most-relied-on (real graph degree; Chunk nodes excluded). An unavailable graph yields empty beds, never invented ones. |
| Agent File Tasks | Current | The executor prompt carries profile-aware file-writing hints (`core/agent_prompts.executor_prompt_for`): the order of operations, that `write_file` comes *first* on a file request, and — for the `compact` profile — the same thing as a three-step numbered list, because the weak-model failure the loop could not repair was writing nothing at all. `EXECUTOR_PROMPT` itself is unchanged, and a Self-Model summary is injected only when a caller passes one — the agent runtime does not yet (see Known Limitations). |
| Agent Profiles | Current | `standard` / `compact` profiles selected from the model id (or `LATTICEAI_AGENT_PROFILE`). Under ~4B the loop shortens its transcript window, escalates corrections sooner, and falls back to writing the plan's files directly when JSON tool calls keep failing. A failed or staged write is reported as *not* written. |
| Folder Memory State | Current | `GET /knowledge-graph/local/health` reports per-folder indexing coverage, failures with their stored reasons, and watch state. An unscanned folder reports unknown, never "0% indexed"; vector freshness is reported once and explicitly labelled global. |
| Voice Capture | Current (transcriber optional) | `POST /api/capture/voice` ingests a memo through the unified pipeline. Transcription is an injected local port: without one the memo is still stored and reported `transcription: "unavailable"` / `searchable: false` — never an invented transcript, never a silent drop. 11.1.0 shares that one port with multi-modal ingestion, so a memo and a scanned `.m4a` can never disagree about whether this machine can hear. The memo itself is still stored through the text door (`source_type: note`) and is unaffected by `allow_multimodal`; the first-class `Audio` node type below applies to recordings the multi-modal router picks up. |
| Multi-modal Memories | Opt-in (default off) | `allow_multimodal` (`LATTICEAI_ALLOW_MULTIMODAL`) routes files by MIME/extension: images become first-class `Image` nodes (content-addressed, idempotent) with `ImageText` OCR children and chunks; recordings become first-class `Audio` nodes (`NodeType.AUDIO`) whose transcript still rides the ordinary text index — chunks, concepts, provenance and dedupe unchanged — carrying `modality`/`audio_path`/`transcription`/`searchable` metadata, and kept with an honest "nobody heard this" body when no transcriber exists. **With the flag off, behaviour is byte-identical to 11.0.x** — the folder-scan allow-list, node ids, and node types are unchanged, and both modes are asserted in `tests/unit/test_t3_ingest_routing.py`. Extraction quality scores what can actually be *retrieved* from a picture (OCR text, caption, vector). |
| Vision OCR / Caption / Embedding | Current (models optional, none bundled) | OCR uses `pytesseract` when installed (absent ⇒ `ocr_status: "unavailable"`, no text). A **caption exists only when a vision-language model wrote one** — 11.1.0 removed `VisionStub`, which synthesized `Image pic.png (PNG 12x8)` from the filename and stored it where a real description would go. Image embeddings come from `VisionEmbeddingProvider` (MLX/CLIP-family or a dotted callable) and have **no hash fallback**: a hashed file path is not a picture, so an unavailable model is reported unavailable. Configure with `LATTICEAI_VISION_PROVIDER` / `_MODEL` / `_SPACE` and `LATTICEAI_VISION_CAPTION_PROVIDER` / `_MODEL`. |
| Image Retrieval | Current (behind the same flag) | Text queries find pictures through their OCR text and captions, which live in the ordinary text index. Image vectors live in a **separate** table and search (`graph/image_vectors.py`) keyed by the vision model, with mismatched widths skipped rather than truncated, and enter `hybrid_search` only by **late fusion** (`image_vector=`, `image_fusion_weight`, default 0.5) — reported in the result's `multimodal` block. `context_quality` gains a `multimodal` key only when image nodes are really in the context, so all-text answers keep the four-key shape. |
| Evidence Thumbnails | Current (minimal) | An `Image` citation in the Evidence panel shows the 96px inline `data:` thumbnail stored on the node at ingest, plus the caption when a model wrote one and an explicit "비전 모델이 없어 설명은 없습니다" line when none did. No new static route and no bypass of the `/local/serve` approval gate: only `data:image/…` is accepted, so an evidence card can never become an outbound request. Thumbnails over 24 KB are dropped rather than shipped. |
| Video Ingestion | **Out of scope in 11.1.0** | Video is *recognized and refused*: an ingest returns `status: "unavailable"` with the reason (`VIDEO_OUT_OF_SCOPE`) rather than storing a file it cannot read. Keyframe extraction and subtitle/transcript alignment (plan §5.2) need a decoder this project does not ship; nothing about video is claimed elsewhere in the product. |
| Frontend Reliability | Current | Core API failures render unavailable states, successful callbacks require successful results, and Vitest/visual tests protect result, proof, conversation, primitive, i18n, and service-error behavior. |
| Trusted Agent Loop | Current | LoopTrace observability + `loop` API payload, python-literal weak-model repair with escalating corrections, deterministic agent-eval CI gate, and proposal-first change governance (`/api/proposals`, 변경 제안 panel) where edits/deletions of existing files are reviewed before applying. |
| Command Center | Current | `/api/command/briefing` + `/api/command/search` aggregate knowledge, conversations, automations, review, health, and suggestions read-only and workspace-scoped; surfaced as the Cmd+K palette and Today's Briefing panel. |
| Evidence → Action | Current | `POST /api/evidence/actions` composes evidence-scoped follow-up prompts (요약/체크리스트/문서/한 페이지) from an answer's real citations; deterministic and model-free, executed through the normal chat path. Unresolvable citations are reported, never dropped. |
| Run Explanation | Current | Every agent run returns a deterministic `explanation` (why it ended, how much the model struggled, one concrete next step). It never upgrades a non-success; `ok` is true only for a verified `DONE`. Rendered on web, VS Code, and Telegram. |
| Project Sessions | Current | `/api/projects` keeps a project's produced files, open TODOs, and last honest verification across runs; `/agent` accepts `project_id` and folds each run's outcome (including the last failure's diagnosis) back in. |
| Citation Precision | Current | A sentence-aware `prose` chunking strategy keeps Korean claims whole for `.txt/.pdf/.docx/.html`; chunk hits carry a locator (`Guide > Setup · p.4`) and stay silent when they cannot prove it. `plain` chunking is byte-identical to the legacy walk. |
| Graph Relation Evidence | Current | Relations record whether they came from a verb or from co-occurrence, with matching weights; enumerations no longer manufacture relation chains, and the curator can demote weak/hub adjacency edges without touching verb-backed or legacy ones. |
| Funnel Alerts | Current | `GET /api/admin/funnel-metrics` returns named, actionable alerts with the value that triggered them; rules stay silent below 10 samples. |
| Frontend Payload | Current | Every route is a `React.lazy` boundary and copy follows the route: `shell` copy registers eagerly, `brain` / `workspace` / `onboarding` register inside the lazy chunk that needs them. Initial static JS is ~99 KiB gzip against a 150 KiB budget. `npm run check:i18n-namespaces` walks the real module graph and fails the build when a chunk reads a key it never imported — the failure mode is otherwise silent, because `t()` returns the raw key and the UI renders an identifier instead of text. |
| Permission Modes | Current | `strict` (default) / `trusted` / `bypass` over the existing ToolRegistry + Change Governor, resolved per user and per workspace and stamped once per agent run (a paused approval resumes under the mode it was approved with). Circuit breakers are mode-invariant: destructive risk, root/home paths, `rm -rf /` style commands, and binary overwrites are denied in every mode. Set it in **설정 → 혼자 해도 되는 일** (`SystemPage` settings tab), on the home screen dial, or through `POST /api/permission-mode`. The reader sees 먼저 물어보기 / 웬만하면 알아서 / 거의 다 알아서 — the wire tokens are unchanged. The selector still renders the server's own catalog rather than a hardcoded mode list (`frontend/src/lib/permissionCopy.ts` supplies the plain wording by mode id and falls back to the server's label for an id it does not know), and refuses to send a `bypass` switch until the risk acknowledgement the server requires is ticked. |
| Network Boundary | Current | `NetworkBoundaryMode` (`local_only` default / `cloud_allowed`) decides whether any knowledge may leave the host, orthogonal to PermissionMode. Set it in **설정 → 내 지식이 나가는 범위** (`NetworkBoundaryPanel`) or through `POST /api/network-boundary`. The selector renders the server's own catalog and refuses to send a `cloud_allowed` switch until the risk acknowledgement the server requires is ticked. A built-in **preview** names the actual memories a given question would send, with its token estimate and whether the token guard would refuse the turn — and works in `local_only` too, labelled as hypothetical. Only the minimal extracted node slice is ever sent, never the graph. Nodes flagged `sensitive` / `private` / `do_not_share` / `local_only` are filtered in **both** modes (mode-invariant, like the agent circuit breakers). |
| Hybrid Cloud Chat | Current (requires cloud key) | When the boundary is `cloud_allowed`, `/chat` branches through `api/chat_hybrid.py` → `services/hybrid_chat.py`: minimal KG context is assembled (`hybrid_context.py`), checked against per-turn and per-session token budgets (`cloud_token_guard.py`), and streamed from an OpenAI-compatible provider (`openai_compatible_adapter.py`, `cloud_streaming.py`). Inert without `LATTICEAI_CLOUD_API_KEY`; the local path is untouched. |
| Cloud Memory Write-Back | Current (proposal-first) | Knowledge extracted from a cloud answer (`cloud_extraction.py`) is enqueued as a Review Center `change_proposal` with provenance. It is written to the graph only when `auto_commit` is explicitly enabled in the hybrid policy (default **false**) and a store write API exists. Multimodal streaming needs both `cloud_allowed` and a separate `allow_multimodal` flag (default **false**). |
| Obsidian Vault Bridge | Current (manual sync) | `POST /api/ingestion/obsidian` reads an *external* Obsidian vault the user approves through the standard local-read approval dance and pushes every `.md` note through the one `IngestionPipeline` gate (`source_type: obsidian`). In-vault `[[wikilinks]]`, `![[embeds]]`, and relative markdown links become `REFERENCES` edges between the note nodes; frontmatter `tags` become workspace-scoped `Topic` nodes joined by `TAGGED_AS`. A link whose target is missing or ambiguous is reported in `links.unresolved`, never guessed. Re-running is idempotent (content-hash dedup plus deterministic edge/topic ids). `dry_run` reports note/link/tag counts without writing. Distinct from the `obsidian_save`/`obsidian_search` tools, which write Lattice's own mirror vault. |
| Selective Brain Network | Opt-in prototype (off by default) | `GET/POST /api/knowledge-graph/share*` export a *chosen* subgraph — node ids, node types, or source types, optionally one hop out — as a bundle signed by this device's Ed25519 identity, with a payload digest pinned inside the signed header. The receiving Brain verifies fail-closed and files every node as a **review proposal** carrying the sender's fingerprint; the graph changes only when a person accepts one item, and an edge into a node the receiver does not have is deferred and reported rather than written dangling. Everything is behind `LATTICEAI_BRAIN_NETWORK` (default off); while off the mutating routes answer 403 with the reason and `GET /api/knowledge-graph/share` still answers `enabled: false`. |
| Release Assets | Current | 11.1.0 package metadata, static app, release notes, current documentation, and exact artifact names are aligned. |

## Known Limitations

- **33 of 50 API routers still raise English literals.** The message catalog
  (`latticeai/core/messages.py`) covers 17 routers as of 10.9.0 — the everyday
  path plus auth/admin/browser. The rest are listed as unmigrated rather than
  quietly claimed: `scripts/check_server_i18n.mjs` prints the remaining count on
  every lint run, and adding a router to its list is how a migration is declared
  finished.
- **A long download no longer freezes the server, but it is still not
  cancellable.** Closing the tab mid-pull leaves the pull running to completion
  on its worker thread.
- **Nothing in the server drives the background embed queue yet.** The queue is
  durable and drains correctly, but the only caller is
  `IngestionPipeline.drain_vector_queue()`. In the normal case this changes
  nothing — the inline sync embeds during the ingest, so new content is
  searchable immediately — and the queue exists for the case where that sync
  fails. Until a scheduler calls it, a failed embedding is retried when someone
  asks for a tick, not on a timer.
- **The HNSW index is rebuilt whole, never appended to.** The `.hnsw` sidecar is
  fingerprinted on the row count and the newest `indexed_at`, so *any* write to
  `vector_embeddings` invalidates it and the next search pays a full rebuild
  (measured in `docs/PERFORMANCE.md` as the "first query" column). That is the
  right trade for a brain that is read far more often than written, and the
  wrong one for a continuous ingest — which is why `brute` remains the default.
- **The quantized backend's memory advantage is not realized in this release.**
  int8 codes are 8x smaller than boxed floats, but the measurement says peak
  memory barely moves (38.4 MB vs 39.7 MB at 10k): the exact scan already feeds
  the index in bounded batches, so resident vectors were never the dominant
  term — the fetched SQLite rows are. What the measurement does show is ~2.2x
  the latency of the exact scan (641 ms vs 293 ms at 10k) for 0.987 recall. It is
  shipped as a working, exhaustive backend and as the representation a held
  index would need; on today's numbers there is no reason to prefer it.
- **Video ingestion is out of scope in 11.1.0.** A video file is *recognized
  and refused* — `status: "unavailable"` with the reason — rather than stored
  as an opaque blob. Keyframe extraction plus subtitle/transcript alignment
  (plan §5.2) needs a decoder this project does not ship, and half of it
  (frames with no audio, or audio with no frames) would be a worse memory than
  none. Images and audio are the whole of this release's multi-modal claim.
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
- **Text queries do not search the image vector space.** A typed question finds
  pictures through their OCR text and captions. The image index answers
  image-to-image similarity and joins `hybrid_search` only when the caller
  supplies a query vector (`image_vector=`), because the two spaces are not
  comparable unless a shared-space model is configured — and no UI supplies one
  yet, so that path is API-only in this release.
- **Obsidian is the only interop bridge in this release.** Notion, email,
  calendar, and Git ingestion were scoped out of 11.1.0 rather than stubbed:
  nothing in the product claims them. They remain on the roadmap and would
  each enter through the same `IngestionPipeline` door the vault bridge uses.
- **The vault bridge is a manual one-shot sync.** There is no watch mode and no
  background scheduling for external vaults: link edges need the node ids only
  a completed inline ingest has, so a "scheduled" run would report edges it
  never wrote. A large vault is capped at 2,000 notes per run and reports
  `truncated: true` when it hits the cap. Only frontmatter `tags` become
  topics; inline `#tags` in note bodies are not parsed.
- **Subgraph sharing is a prototype, and its encryption is a shared
  passphrase.** The bundle is signed by the sending device (Ed25519) and
  encrypted with the same PBKDF2 + AES-GCM mechanism as `.latticebrain`
  archives. Encrypting *to a recipient's public key* is not implemented —
  `GET /api/knowledge-graph/share` reports
  `recipient_public_key_encryption: false` rather than implying otherwise.
  Accepting a proposal merges one node at a time; there is no bulk accept, and
  the receiving UI is the existing Review Center rather than a dedicated
  share screen.
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
- **The Self-Model summary reaches document generation, not the agent loop.**
  `context_builder` injects it (opt-in per call, on by default), and
  `executor_prompt_for(self_model_summary=…)` accepts it — but the agent runtime
  holds its executor prompt as a fixed string, so wiring the profile into a run
  needs a new port on `AgentDeps` that this release does not add.
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
- **The approval card under 작업 → 실행 still labels raw payload fields**
  (`Action`, `Action Label`, `User Email`) in plain mode. It is visible in
  `output/release/v10.6.3/screenshots/09-automation-runs.png`, published rather
  than cropped, and is the first item for the next plain-language pass. 10.6.1
  moved that card to the top of the screen, which makes the raw labels the most
  visible text on it — the placement is fixed, the wording is not.
- The plain-mode vocabulary sweep in `tests/visual/v3.spec.js` checks a word
  list over ten routes. It catches the engine's vocabulary reaching a reader;
  it cannot catch a sentence that is jargon-free and still unclear.
- SQLite is the live local Brain store. PostgreSQL/pgvector remains optional
  scale/migration tooling and requires explicit setup.
- Package registry publishing is owner-run and can lag behind the GitHub
  release.
- Docker setup, model downloads, cloud model calls, Telegram, Brain Network,
  update checks, and marketplace refreshes are explicit opt-in paths.
- Agent/workflow simulation without a loaded LLM is deterministic and must stay
  labeled as model-free rather than autonomous model execution.
- Local file privacy depends on the user's OS account, disk encryption, and
  backup policy outside Lattice AI.
- Surface parity has no recorded gaps as of 9.9.7; every remaining "—" in
  `docs/SURFACE_PARITY.md` is a design boundary that states its reason (e.g.
  approval *decisions* stay off the browser extension because they need a
  signed, single-use, short-TTL token).
- Voice transcription ships with **no bundled transcriber**. Memos are stored
  and honestly marked not-searchable until a local transcriber is wired in;
  `GET /api/capture/voice/status` reports which case applies.
- The compact agent profile is chosen from the model id. A model whose id names
  no size keeps the standard loop, so an unlabelled small model needs
  `LATTICEAI_AGENT_PROFILE=compact`.
- The conversation artifact ledger is process-local and bounded — it answers
  "what did this conversation just make?" for minutes, not days. After a
  restart, normal retrieval covers it because indexing has caught up.
- Requirement coverage blocks completion only for *declared* project files;
  matching a prose feature request to a transcript stays the critic's
  judgement and is advisory.
- Root compatibility shims were removed in 9.9.1 (only `server.py` remains
  for `uvicorn server:app`); the managed compatibility inventory tracks the
  removals, internal-only Brain shim layers were removed in 8.8.0, and the
  legacy debt gate in `npm run lint` blocks reintroduction.

## Release-Era History Kept In Git

The Git tree keeps release history from:

- 9.9.0
- 9.8.0
- 9.7.0
- 9.6.0
- 9.5.0
- 9.4.0
- 9.3.0
- 9.2.0
- 9.1.0
- 9.0.0
- 8.9.0
- 8.8.0
- 8.7.0
- 8.6.0
- 8.5.0
- 8.4.0
- 8.3.0
- 8.2.0
- 8.1.0
- 8.0.0

Release notes and release evidence older than 8.0.0 are intentionally removed
from the tracked tree.
