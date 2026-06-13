# v4.5.0 Graph UX Report

Date: 2026-06-13

## Goal

Improve discoverability, readability, focus, searching, and filtering without
redesigning the graph architecture.

## Changes

- Graph copy now describes ideas, relationships, sources, and saved items
  instead of backend endpoints.
- Basic mode hides raw node IDs in the focus panel and shows connections/source
  instead.
- Search placeholder is product-oriented in Basic mode.
- Brain search copy explains unified memory/graph/document search without
  exposing endpoint mechanics.
- Advanced/Admin retain structured inspection where useful.

## Existing Capabilities Preserved

- Cytoscape graph rendering
- semantic groups
- search
- label modes
- importance filter
- focus neighborhoods
- group collapse/expand
- backend hybrid search

## Evidence

Screenshot: `output/audits/v4.5.0-rc/screenshots/04-graph-basic-focus.png`
