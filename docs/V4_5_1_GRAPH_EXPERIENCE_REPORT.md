# v4.5.1 Graph Experience Report

## Goal

Knowledge exploration should feel like using a Digital Brain, not inspecting a
database viewer.

## Reimagined Entry Point

Brain is now presented as Home. The default tab is Today, with the map still one
click away. The first-viewport language now explains that Lattice turns added
material into a living memory map and keeps every answer tied to its source.

## Preserved Capability

- Cytoscape graph explorer.
- Semantic groups.
- Search, focus, label modes, group collapse, and importance filtering.
- Provenance coverage.
- Hybrid search.
- Portability and backup controls.

## Presentation Changes

- Page copy avoids endpoint/framework language in Calm mode.
- Graph remains real and API-backed.
- Existing graph route aliases still open the map directly.

## Evidence

- Graph UI: `frontend/src/pages/Brain.tsx`
- Visual test: `knowledge graph renders a Cytoscape canvas and provenance coverage`
- Related historical report: `docs/V4_5_0_GRAPH_UX_REPORT.md`
