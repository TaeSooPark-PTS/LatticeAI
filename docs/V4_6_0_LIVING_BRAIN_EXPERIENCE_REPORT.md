# v4.6.0 Living Brain Experience Report

## Goal

v4.6.0 stops presenting Lattice AI as a graph product or dashboard. The
first-run product flow is Login -> Environment Analysis -> Recommended Models
-> Install & Load -> Brain. After setup, the Brain is the center of the desktop
experience, and the graph is repositioned as an advanced layer for intentional
relationship exploration.

## Product Changes

- First launch opens to a premium minimal Login screen only.
- Setup runs as a full-screen guided sequence: friendly environment analysis,
  short ranked model recommendations, and install/download/validate/load.
- After model load, `/app` and `/app#/brain` open into Brain plus conversation.
- The living Brain remains visible during primary conversation and reacts to
  listening, recall, thinking, and active agent/workflow signals.
- `/app#/ask` and `/app#/chat` remain compatible but route to the Brain
  conversation.
- Primary navigation is reduced to Brain, Memory, Files, Automations, Models,
  and Settings.
- Brain tabs now follow the product ladder: Brain, Memories, Knowledge,
  Relationships, Graph, Care.
- First-run setup no longer appears as dashboard cards above any app page; it
  gates the app before the Brain opens.

## Architecture

- Added `frontend/src/components/LivingBrain.tsx` as the animated Brain
  presence component.
- Added `frontend/src/components/BrainConversation.tsx` to centralize chat
  streaming, history, image attachment, model status, memory previews, and
  Brain activity state.
- Added `frontend/src/components/ProductFlow.tsx` to own Login, environment
  analysis, recommendation, install/download/validate/load, and Brain entry.
- Kept graph parsing, Cytoscape rendering, search, provenance, portability, and
  archive APIs intact.
- Kept FastAPI, Tauri, StorageEngine, Brain Core, backup/restore, and
  portability unchanged.

## Compatibility

- Existing backend routes are unchanged.
- Legacy hash routes continue to resolve through the SPA route alias table.
- The advanced graph remains available at `/app#/knowledge-graph`.
- The old Ask page imports the shared Brain conversation component to avoid
  duplicate chat behavior.

## Validation Scope

The v4.6.0 work should be validated with:

- frontend lint/typecheck/build
- Python syntax validation
- affected visual tests
- unit tests covering version consistency and route compatibility
- Tauri cargo check when the desktop toolchain is available
