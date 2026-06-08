# Lattice AI v3.3.1 Visual Rebuild Notes

## Summary

v3.3.1 visibly rebuilds the `/app` frontend while preserving existing runtime
behavior. The work is not a feature expansion: it changes the product shell,
navigation hierarchy, tokens, components, and primary view composition so the
application reads as a premium local-first AI workspace rather than a generic
dashboard.

## Changed

- Rebuilt the global app shell with a denser command rail, grouped navigation,
  local index readiness footer, quieter topbar, and mode-aware command palette.
- Reorganized production navigation:
  - Basic: Home, Chat, Files, Search, Knowledge, Memory, Models, Settings.
  - Advanced: Agents, Workflows, Skills, Hooks, MCP.
  - Admin: Users, Permissions, Audit Logs, Security, Policies, Private VPC.
- Kept Pipeline, Planning, My Computer, Marketplace, and Tools deep-linkable for
  compatibility, but removed them from first-class production navigation.
- Replaced the v3.3.0 visual palette with cooler neutral light/dark tokens.
- Tightened the component system: 8px card/panel radius, compact controls,
  denser tables, redesigned stat cards, and improved empty states.
- Rebuilt Home as a readiness dashboard for backend, model, retrieval, memory,
  connected sources, workspace stats, and recent activity when available.
- Clarified Files by separating available manual upload from desktop local-agent
  folder connection.
- Improved Chat send/stop behavior so the streaming button uses one stable
  handler.
- Added Settings runtime readiness for backend, desktop local agent, model
  runtime, host telemetry, and embedding provider configuration.

## Runtime QA Targets

The visual pass must verify these local routes:

- `/app#/home`
- `/app#/chat`
- `/app#/files`
- `/app#/models`
- `/app#/hybrid-search`
- `/app#/knowledge-graph`
- `/app#/memory`
- `/app#/agents`
- `/app#/workflows`
- `/app#/settings`

## Validation Notes

- `npm run build:assets` regenerated content-hashed CSS/JS and
  `static/v3/asset-manifest.json` at version `3.3.1`.
- Full validation results are captured in the final task report.

## Follow-Up

- Add automated a11y checks around contrast and keyboard focus once the visual
  rebuild settles.
- Consider promoting a desktop local-agent health endpoint so Files can report a
  live agent status instead of a build-level disabled explanation.
