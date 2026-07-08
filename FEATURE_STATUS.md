# Lattice AI Feature Status (v9.0.0)

Current release: **9.0.0 — Code Review Closure & Runtime Cleanup**.

This file describes the current product state and known limitations. Historical
change history is intentionally limited to 8.0.0-9.0.0 in `RELEASE.md` and
`docs/CHANGELOG.md`.

## Product Position

Lattice AI is a local-first Digital Brain. The durable asset is the user's
Brain: conversations, documents, decisions, memories, provenance, and Knowledge
Graph structure. Models are replaceable voices over that Brain.

The main product surface is not an admin dashboard. The 9.0.0 line keeps the
living Brain and composer in the first screen while closing July 8 review
follow-ups, tightening chat/runtime reliability, and keeping release artifacts
aligned.

## Current Feature Status

| Area | Status | Notes |
| --- | --- | --- |
| Brain Home | Current | Living Brain, composer, and Brain Brief are visible in the first viewport on desktop and mobile. |
| Brain Brief | Current | MemoryService turns real workspace, conversation, graph, vector, and source-health signals into focus, evidence, and next actions. |
| Conversation | Current | Chat is the primary action. It refuses to fake model output when no model is loaded, surfaces memory proof when context exists, and routes explicit file actions into the governed workspace file tool. |
| Knowledge Graph | Current | Memory graph exploration, graph read compatibility, provenance-aware retrieval, workspace-scoped reads/traversal, workspace-safe duplicate content, and KG v2 equivalence gates remain active. |
| Source Capture | Current | Files, folders, notes, and web/source capture paths feed Brain memory and graph context through explicit user actions and the unified ingestion pipeline when available. |
| Local Models | Current | Setup and model recommendation flow remains explicit; model downloads and runtime installs require user action. |
| Installer Audit | Current | Setup Wizard, auto setup, and engine installers expose redacted command plans, require confirmation tokens, and write local process audit events. |
| Cloud Models | Opt-in | Cloud prompts are sent only after keys are configured and the user selects a cloud model path. |
| Agent Runtime | Current | AgentRuntime preview/readiness contracts avoid tool execution during preview, reject unknown roles, require explicit human approval for non-auto-approved plans, tolerate legacy run events with contract envelopes, and expose orchestration boundaries. |
| Tool Registry / MCP | Current | ToolRegistry diagnostics, direct HTTP/MCP policy gates, and MCP install state are separated from app-factory helpers and covered by focused tests. |
| Workspaces | Current | Personal workspace is default. Organization/admin surfaces remain separated from normal Brain use. |
| VS Code Extension | Current | Sync/status endpoints expose connection and indexing state. File contents move only through explicit user actions. |
| Release Assets | Current | 9.0.0 package metadata, static app, release notes, current documentation, and exact artifact names are aligned. |

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
- Legacy root shims remain for compatibility while implementation continues to
  move into focused `latticeai.*` and `lattice_brain.*` modules; remaining
  shims are tracked by the managed compatibility inventory, and internal-only
  Brain shim layers were removed in 8.8.0.

## Release-Era History Kept In Git

The Git tree keeps release history from:

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
