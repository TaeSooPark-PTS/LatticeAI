# v4.6.0 Living Brain Experience Report

## Goal

v4.6.0 stops presenting Lattice AI as a graph product or dashboard. The
first-run product flow is Login -> Environment Analysis -> Recommended Models
-> Install & Load -> Brain. After setup, the Brain is the center of the desktop
experience, and the graph is repositioned as the deepest layer inside Brain
exploration rather than a separate destination.

## Product Changes

- First launch opens to a premium minimal Login screen only.
- Setup runs as a full-screen guided sequence: friendly environment analysis,
  short ranked model recommendations, and install/download/validate/load.
- After model load, `/app` and compatible legacy entry URLs open into Brain plus
  conversation.
- The living Brain is an anatomical, recognizable Brain that remains visible
  during primary conversation and reacts to listening, recall, thinking,
  planning, and active agent/workflow signals.
- Brain exploration now has five depths:
  Living Brain, Memory Layer, Knowledge Layer, Relationship Layer, and
  Knowledge Graph.
- The Knowledge Graph appears only at Level 5 and includes nodes, edges, search,
  and focus details.
- `/chat`, `/ask`, `/graph`, and other legacy entry URLs remain compatible app
  entry points, but the primary user path inward is through the Brain itself.
- First-run setup no longer appears as dashboard cards above any app page; it
  gates the app before the Brain opens.

## Architecture

- Added `frontend/src/components/LivingBrain.tsx` as the animated Brain
  presence component.
- Updated `frontend/src/App.tsx` to own the five-depth Brain journey and the
  emergent graph surface.
- `frontend/src/components/BrainConversation.tsx` remains available for legacy
  page compatibility and now shares the expanded Brain activity states.
- Added `frontend/src/components/ProductFlow.tsx` to own Login, environment
  analysis, recommendation, install/download/validate/load, and Brain entry.
- Kept graph APIs, memory APIs, search, provenance, portability, and archive
  APIs intact.
- Kept FastAPI, Tauri, StorageEngine, Brain Core, backup/restore, and
  portability unchanged.

## Compatibility

- Existing backend routes are unchanged.
- Legacy hash routes and redirects continue to arrive inside the SPA.
- The advanced graph capability remains available as Level 5 of Brain
  exploration.
- The older route components remain in source for compatibility while the
  primary app surface is the Brain Space.

## Validation Scope

The v4.6.0 work should be validated with:

- frontend lint/typecheck/build
- Python syntax validation
- affected visual tests
- unit tests covering version consistency and route compatibility
- Tauri cargo check when the desktop toolchain is available

Validated in this update:

- `npm run lint`
- `npm run test:visual`
- `npm run check:python`
- `npm run test:unit`
- `npm run test:integration` with the local server on port 8899
- `npm run build`
