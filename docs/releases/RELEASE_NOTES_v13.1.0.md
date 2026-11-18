# Lattice AI 13.1.0 — Living Synthesis

> **Status: historical** — point-in-time release note.

2026-11-18

**13.1.0 Living Synthesis** is a major user-facing capability upgrade across Knowledge Graph exploration & epistemic conflict resolution, live Chat + Agent workspace actionability, and daily-use ingestion, download, search, and bilingual (`ko`/`en`) ergonomics — backed end-to-end by native Rust and Python endpoints at **100% Vitest coverage**.

## What landed

- **Interactive Knowledge Graph Inspector, Scoped Multi-Hop Synthesis & Contradiction Resolution (`neighborhood_synthesis.rs`, `GraphNodeInspector.tsx`, `ContradictionInspector.tsx`):**
  - `rust/lattice-retrieval/src/neighborhood_synthesis.rs` and `search_api/handlers.rs`: `GET /api/graph/node` (`POST /api/graph/node`) supports `include_reasoning=true` alongside `include_neighbors=true`, computing deterministic 1-hop/2-hop `reasoning_paths`, intermediate `bridge_concepts`, and `CONTRADICTS` / `SUPERSEDES` conflict edges directly from the scoped neighborhood in a single SQLite read.
  - `frontend/src/pages/brain/{GraphNodeInspector.tsx,DigitalBrainExplorer.tsx,graphExplorer.ts}` and `frontend/src/features/brain/MultiHopSynthesisView.tsx`: Selecting any node loads live confidence/provenance, clickable 1-hop neighbors, embedded 2-hop synthesis with hop/conflict filter pills and confidence bars, full source excerpt inspection (`SourceChunkModal`), contextual "Ask Brain" prompt handoff, a toolbar conflict-only filter (`Conflicts`), and `/` + `Escape` keyboard ergonomics.
  - `frontend/src/features/brain/{ContradictionInspector.tsx,BrainIntelligencePanel.tsx}`: Inspect detected contradiction pairs inside Brain Intelligence, jump directly to conflicting nodes in the Knowledge Graph, stage review items (`POST /api/brain/contradictions/propose`), and resolve them (`keep_old`, `replace`, `keep_both_temporal` via `POST /api/brain/contradictions/resolve`) in one click.
- **Tangible Chat & Agent Workspace Experience (`BrainConversation.tsx`, `InlineStagedProposals.tsx`, `AnswerProof.tsx`, `AgentStepTimeline.tsx`):**
  - One-click **Save to Brain** on assistant answers promotes synthesized insights directly into the Knowledge Graph via `POST /api/ingest/note`.
  - **Inline Staged Proposals** appear directly beneath agent turns that stage change proposals, displaying diffs and one-click **Approve** / **Reject** controls in context without leaving the conversation.
  - `AgentStepTimeline.tsx` surfaces active tool names live during streaming and tool-run counts on completion, while `SourceChunkModal` (`AnswerProof.tsx`) adds one-click **Inspect in Knowledge Graph** navigation and consent-gated original image evidence preview.
- **Live Embedding Drain, Cooperative Download Cancel & Bilingual Daily-Use Ergonomics (`health.rs`, `BrainSignals.tsx`, `InstallScreen.tsx`, `ModelsPanel.tsx`, `CommandPalette.tsx`, `WorkspaceProfileSwitcher.tsx`):**
  - `rust/lattice-retrieval/src/brain_api/health.rs` preserves `stale_embedder` in `/api/brain/vector-freshness`, while `VectorFreshnessNotice` (`BrainSignals.tsx`) renders an `embedded / total` progress bar and a one-click **Index now** button wired to `POST /api/index/drain`.
  - `InstallScreen.tsx`, `ModelsPanel.tsx`, `useLibraryModels.ts`, and `recommendationModel.ts` add human-readable byte/file download metrics (`MB / GB`, `current_file`), cooperative download cancellation (`AbortController` + `DELETE /models/unload/{model_id}`), and full Korean/English error localization with zero English leaks.
  - `CommandPalette.tsx` (`⌘K`) jumps straight onto the selected Knowledge Graph node (`focusedGraphNodeId`) and adds quick language toggle & Graph Explorer actions; `WorkspaceProfileSwitcher.tsx` invalidates active workspace queries immediately upon switching.

## Artifacts

- `dist/ltcai-13.1.0-py3-none-any.whl`
- `dist/ltcai-13.1.0.tar.gz`
- `ltcai-13.1.0.tgz`
- `dist/ltcai-13.1.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_13.1.0_aarch64.dmg`
