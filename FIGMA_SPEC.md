# Lattice AI Figma-Equivalent Spec (v6.1.0)

This repository does not currently contain an editable Figma component library.
For v6.1.0, this document is the implementation-grade product spec used in
place of a completed Figma library. The production source remains the tokenized
`/app` implementation in `static/css/tokens.css` and `static/v3/css/*.css`.

## Product Direction

- Premium local-first AI workspace, closer to Linear, Raycast, and ChatGPT than
  an enterprise admin dashboard.
- Dense, quiet, readable, trustworthy.
- No fake readiness: every runtime surface is live, unavailable, pending, or
  explicitly disabled.
- Basic mode: Home, Chat, Files, Search, Knowledge, Memory, Models, Settings.
- Advanced mode: Agents, Workflows, Skills, Hooks, MCP.
- Admin mode: Users, Permissions, Audit Logs, Security, Policies, Private VPC.

## Shell

- Left command rail, 292px desktop, 76px collapsed tablet, off-canvas mobile.
- Brand block: compact mark, "Lattice AI", "Private runtime".
- Workspace selector directly below brand.
- Grouped navigation with compact rows, icons, title, and short metadata.
- Footer shows retrieval readiness, account, and theme toggle.
- Topbar is low-noise: breadcrumbs, command palette, index chip, mode switcher.

## Components

- Cards and panels use 8px radius or less.
- Buttons are compact, icon-led, and token-colored.
- Tables use sticky headers, compact rows, and tokenized hover states.
- Inputs/selects/textareas share a 38px control height and focused token ring.
- Empty states include icon, title, truthful reason, and action when available.
- Status pills always identify ready, pending, idle, failed, live, or unavailable
  without inventing data.

## Primary Views

- Home: local readiness dashboard for backend, model, retrieval, memory, index
  sources, quick actions, and recent activity if available.
- Chat: native three-pane chat with conversation rail, grounded thread,
  composer, retrieval context, streaming state, and no-model error state.
- Files: manual upload is available; folder connection is visible but truthfully
  disabled without the desktop local agent.
- Search: hybrid search shows keyword, vector, and graph scores.
- Knowledge: SVG graph explorer plus entity inspector.
- Memory: recall, source tiers, inspect, compact, and rebuild surfaces.
- Models: active model, embedding provider, catalog, and load/unload actions.
- Settings: appearance, workspace, runtime readiness, embeddings, integration
  probes, and About version from `/health`.

## Responsive Requirements

- 390px mobile must have no horizontal overflow on Home, Knowledge, Search,
  Settings, and Admin routes.
- Chat conversation/context panes become drawers on tablet/mobile.
- Fixed-format controls keep stable dimensions across hover/loading states.

## Asset Rule

Run `npm run build:assets` after changing `static/css/tokens.css`,
`static/v3/css/*.css`, or `static/v3/js/**/*.js`. The manifest version must
match `package.json`.
