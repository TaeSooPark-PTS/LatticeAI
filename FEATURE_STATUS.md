# Lattice AI Feature Status (v9.9.5)

> **Status: canonical** — current-truth feature state, kept in sync with the
> current release.

Current release: **9.9.5 — Closed Gaps**.

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
device analysis no longer fabricates a "ready" model card on probe failure.

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
| VS Code Extension | Current | Sync/status endpoints expose connection and indexing state. File contents move only through explicit user actions. |
| Frontend Reliability | Current | Core API failures render unavailable states, successful callbacks require successful results, and Vitest/visual tests protect result, proof, conversation, primitive, i18n, and service-error behavior. |
| Trusted Agent Loop | Current | LoopTrace observability + `loop` API payload, python-literal weak-model repair with escalating corrections, deterministic agent-eval CI gate, and proposal-first change governance (`/api/proposals`, 변경 제안 panel) where edits/deletions of existing files are reviewed before applying. |
| Command Center | Current | `/api/command/briefing` + `/api/command/search` aggregate knowledge, conversations, automations, review, health, and suggestions read-only and workspace-scoped; surfaced as the Cmd+K palette and Today's Briefing panel. |
| Release Assets | Current | 9.9.0 package metadata, static app, release notes, current documentation, and exact artifact names are aligned. |

## Known Limitations

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
