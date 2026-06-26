# Lattice AI Feature Status (v8.1.0)

Current release: **8.1.0 - Intuitive Brain Home**.

This file describes the current product state and known limitations. Historical
change history is intentionally limited to 7.0.0-8.1.0 in `RELEASE.md` and
`docs/CHANGELOG.md`.

## Product Position

Lattice AI is a local-first Digital Brain. The durable asset is the user's
Brain: conversations, documents, decisions, memories, provenance, and Knowledge
Graph structure. Models are replaceable voices over that Brain.

The main product surface is not an admin dashboard. The 8.1.0 Brain Home puts
the living Brain, recent memory, connected topic, next action, and conversation
composer in the first screen so users can immediately understand what to do.

## Current Feature Status

| Area | Status | Notes |
| --- | --- | --- |
| Brain Home | Current | Living Brain, memory narrative, connected topic, next action, and composer are visible in the first viewport on desktop and mobile. |
| Conversation | Current | Chat is the primary action. It refuses to fake model output when no model is loaded and can surface memory proof when context exists. |
| Knowledge Graph | Current | Memory graph exploration, graph read compatibility, provenance-aware retrieval, and KG v2 equivalence gates remain active. |
| Source Capture | Current | Files, folders, notes, and web/source capture paths feed Brain memory and graph context through explicit user actions. |
| Local Models | Current | Setup and model recommendation flow remains explicit; model downloads and runtime installs require user action. |
| Cloud Models | Opt-in | Cloud prompts are sent only after keys are configured and the user selects a cloud model path. |
| Agent Runtime | Current | AgentRuntime preview/readiness contracts avoid tool execution during preview and expose blocking reasons instead of simulated success. |
| Tool Registry / MCP | Current | ToolRegistry diagnostics and MCP install state are separated from app-factory helpers and covered by focused tests. |
| Workspaces | Current | Personal workspace is default. Organization/admin surfaces remain separated from normal Brain use. |
| VS Code Extension | Current | Sync/status endpoints expose connection and indexing state. File contents move only through explicit user actions. |
| Release Assets | Current | 8.1.0 package metadata, static app, screenshots, GIF/WebM evidence, and release notes are aligned. |

## Known Limitations

- SQLite is the default local Brain store. PostgreSQL/pgvector remains optional
  scale mode and requires explicit setup.
- Package registry publishing is owner-run and can lag behind the GitHub
  release.
- Docker setup, model downloads, cloud model calls, Telegram, Brain Network,
  update checks, and marketplace refreshes are explicit opt-in paths.
- Agent/workflow simulation without a loaded LLM is deterministic and must stay
  labeled as model-free rather than autonomous model execution.
- Local file privacy depends on the user's OS account, disk encryption, and
  backup policy outside Lattice AI.
- Legacy root shims remain for compatibility while implementation continues to
  move into focused `latticeai.*` and `lattice_brain.*` modules.

## Release-Era History Kept In Git

The Git tree keeps release history from:

- 8.1.0
- 8.0.0
- 7.9.0
- 7.8.0
- 7.7.0
- 7.6.0
- 7.5.0
- 7.4.0
- 7.3.0
- 7.2.0
- 7.1.0
- 7.0.0

Release notes and release evidence older than 7.0.0 are intentionally removed
from the tracked tree.
