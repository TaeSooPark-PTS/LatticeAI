# Lattice AI v4.3.2 Graph UX Report

Date: 2026-06-13

## Scope

v4.3.2 overhauls the Brain graph user experience without changing Brain Core,
storage, or the FastAPI graph contract. The Brain page continues to consume the
existing generated OpenAPI client and `/knowledge-graph/graph`.

## Implemented

- Semantic graph groups: Knowledge, Sources, Activity, Memory, People, System,
  and Other.
- Node styling by type, importance score, degree, and focus state.
- Local search across title, id, summary, type, and metadata.
- Backend hybrid-search integration for graph-related queries.
- Minimum importance filtering with an honest visible count.
- Group collapse/expand that preserves permanent access to hidden groups.
- Focused neighborhoods from selected nodes.
- Label mode controls for names, type, ids, and hidden labels.
- Important-node list driven by graph degree and importance.

## Data Contract

The graph explorer uses existing runtime data only:

- `/knowledge-graph/graph` for nodes and edges.
- `/api/search/hybrid` for backend search results.
- Existing stats, index status, memory, provenance, portability, backup, and
  storage APIs for surrounding Brain panels.

No demo graph, static graph, or placeholder graph data is used.

## Evidence

- Initial graph: `output/audits/v4.3.2-rc/screenshots/02-graph-explorer-before.png`
- Search: `output/audits/v4.3.2-rc/screenshots/03-graph-search.png`
- Group collapse: `output/audits/v4.3.2-rc/screenshots/04-graph-collapse-group.png`
- Focus neighborhood: `output/audits/v4.3.2-rc/screenshots/05-graph-focus-neighborhood.png`
- Walkthrough GIF: `output/audits/v4.3.2-rc/gifs/graph-product-walkthrough.gif`
- Seeded graph log: `output/audits/v4.3.2-rc/logs/graph-after-upload.json`

## Result

PASS. The Brain graph is a real interactive view over persisted user data. The
self-audit seeded the graph through the upload API, verified the resulting graph
state, and exercised search, focus, collapse, backup, and portability flows.
