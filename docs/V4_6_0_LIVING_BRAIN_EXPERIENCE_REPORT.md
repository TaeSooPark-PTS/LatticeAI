# v4.6.0 Living Brain Experience Report

## Goal

v4.6.0 stops presenting Lattice AI as a graph product. The Brain is the center
of the desktop experience, and the graph is repositioned as an advanced layer
for intentional relationship exploration.

## Product Changes

- The default `/app` and `/app#/brain` screen now opens directly into Brain plus
  conversation.
- The living Brain remains visible during primary conversation and reacts to
  listening, recall, thinking, and active agent/workflow signals.
- `/app#/ask` and `/app#/chat` remain compatible but route to the Brain
  conversation.
- Primary navigation is reduced to Brain, Add, Automate, Library, and Care.
- Brain tabs now follow the product ladder: Brain, Memories, Knowledge,
  Relationships, Graph, Care.
- First-run setup no longer appears above the Brain home screen; it remains
  available through onboarding/system routes.

## Architecture

- Added `frontend/src/components/LivingBrain.tsx` as the animated Brain
  presence component.
- Added `frontend/src/components/BrainConversation.tsx` to centralize chat
  streaming, history, image attachment, model status, memory previews, and
  Brain activity state.
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
