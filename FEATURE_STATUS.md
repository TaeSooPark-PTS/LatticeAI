# Lattice AI Feature Status (v10.9.0)

> **Status: canonical** — current-truth feature state, kept in sync with the
> current release.

Current release: **10.9.0 — Never Blocks**.

This file describes the current product state and known limitations. Historical
change history is intentionally limited to 8.0.0-9.9.0 in `RELEASE.md` and
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

The 10.9.0 line is about the one event loop this server runs. Pulling a model,
installing an engine, installing an MCP package and sampling host capacity for
the System screen were all executed directly inside `async def` handlers, so for
the duration of any of them the process answered nothing else — a model
download's timeout is 900 seconds. All five paths hand their blocking body to a
worker thread; ruff's `ASYNC` blocking-call rules and
`tests/unit/test_event_loop_not_blocked.py` (which runs a ticker coroutine
during the handler and asserts the loop kept getting control) keep it that way.
The same release makes keyboard focus visible on the capture pills again,
stops the organism sitting in "thinking" after it has answered, and moves
fourteen more routers onto the server message catalog so an error arrives in
the language the reader chose.

## Current Feature Status

| Area | Status | Notes |
| --- | --- | --- |
| Brain Home | Current | Living Brain, composer, and Brain Brief are visible in the first viewport on desktop and mobile. |
| Automation Intelligence | Current | /api/automation mines recurring user questions (deterministic local clustering, literal-question evidence) and connected knowledge folders into one-click suggestions; installs are idempotent, disabled-draft, review-queue-gated workflows. |
| Brain Intelligence | Current | The Brain diagnoses itself: /api/brain health scoring (freshness, connectivity, search readiness, consistency), proactive insights digest, contradiction surfacing, and consent-first duplicate consolidation, wired from the lattice_brain quality layer and covered by unit + live-boot tests. |
| Hybrid Recall | Current | /api/memory/recall and the graph-layer `hybrid_search` blend lexical evidence with vector similarity (hybrid-evidence/v2 gate) with workspace-scoped vector hits and honest lexical fallback when the vector tier fails. Chat consumes a `context_quality` signal so grounding reflects how strong the retrieved context actually is. |
| Folder Ingestion | Current | `ingest_folder` indexes a chosen local folder with `.latticeignore` filtering; long runs execute as resumable background jobs surfaced through `/api/ingestion/jobs` rather than a single blocking request. |
| Extraction Quality | Current | Ingestion scores per-source `extraction_quality` and runs an observe-mode `quality_gate` that flags low-quality extractions instead of silently accepting them. |
| Vector Freshness | Current | `/api/brain/vector-freshness` reports embedded-vs-total content so stale embeddings are visible and reindexing can be triggered on demand. |
| Change Governance | Current | `core/tool_governor.py` `MUTATING_TOOL_INVENTORY` requires every mutating tool to be governed or explicitly exempt (release-checked). File edits/deletions flow through change proposals that record a base content hash and re-check it for conflicts before applying atomically. `core/agent_eval.py` verifier fails closed to `NEEDS_REVIEW` on unverifiable or failing outcomes. |
| Brain Brief | Current | MemoryService turns real workspace, conversation, graph, vector, and source-health signals into focus, evidence, and next actions. |
| Conversation | Current | Chat is the primary action. It refuses to fake model output when no model is loaded, surfaces memory proof when context exists, and routes explicit file actions into the governed workspace file tool. |
| Knowledge Graph | Current | Memory graph exploration, graph read compatibility, provenance-aware retrieval, fail-closed workspace reads/traversal, explicit legacy-global compatibility, workspace-safe duplicate content, and KG v2 equivalence gates remain active. |
| Source Capture | Current | Files, folders, notes, and web/source capture paths feed Brain memory and graph context through explicit user actions and the unified ingestion pipeline when available. |
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
| Agent Profiles | Current | `standard` / `compact` profiles selected from the model id (or `LATTICEAI_AGENT_PROFILE`). Under ~4B the loop shortens its transcript window, escalates corrections sooner, and falls back to writing the plan's files directly when JSON tool calls keep failing. A failed or staged write is reported as *not* written. |
| Folder Memory State | Current | `GET /knowledge-graph/local/health` reports per-folder indexing coverage, failures with their stored reasons, and watch state. An unscanned folder reports unknown, never "0% indexed"; vector freshness is reported once and explicitly labelled global. |
| Voice Capture | Current (transcriber optional) | `POST /api/capture/voice` ingests a memo through the unified pipeline. Transcription is an injected local port: without one the memo is still stored and reported `transcription: "unavailable"` / `searchable: false` — never an invented transcript, never a silent drop. |
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
| Release Assets | Current | 10.9.0 package metadata, static app, release notes, current documentation, and exact artifact names are aligned. |

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
