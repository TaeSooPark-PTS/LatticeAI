# Release Notes

## v2.2.1 - Frontend / UX Overhaul Release

Lattice AI v2.2.1 is a frontend and UX release for the local-first AI
workspace. It makes knowledge graph, AI pipeline, model workflow, and
multi-agent coding surfaces easier to use across screen sizes while preserving
feature behavior.

### Highlights

- Mobile-first responsive UI across phone, tablet, laptop, desktop, ultrawide,
  and 4K. Content is re-laid out for smaller screens, never hidden.
- Light/dark mode with OS detection, a manual toggle, and persistence.
- Rebuilt design-token system as a single source of truth
  (`static/css/tokens.css`); no `!important`-based theming.
- Accessibility: 44px touch targets, `:focus-visible` rings, a keyboard-safe
  chat composer (visualViewport inset), iOS no-zoom inputs, and reduced-motion
  support.
- Knowledge Graph UX: responsive canvas that re-fits on resize, zoom buttons,
  fullscreen, minimap, relationship filter, mobile graph/card view, and a
  theme-aware palette.
- Admin UX: wide tables reflow to cards on mobile, with responsive layout,
  dark/light support, and larger touch targets.
- File UX: drag & drop and screenshot paste to attach files.
- Model cards show country, company, run mode, and internet usage in plain
  language.

### GitHub Release Copy

Local-first AI workspace for knowledge graphs, AI pipelines, and multi-agent
coding workflows.

Plan, execute, review, and remember work across local models, cloud models,
files, and team workflows.

This release refreshes the v2.2.1 workspace UI and marketplace-facing
positioning around:

- Local-first AI Workspace
- AI Pipeline Platform
- Knowledge Graph Platform
- Multi-Agent Workflow
- Personal / Organization Workspace
- Local Model Management
- SSO for teams

### Expected Artifacts

```text
dist/ltcai-2.2.1-py3-none-any.whl
dist/ltcai-2.2.1.tar.gz
dist/ltcai-2.2.1.vsix
ltcai-2.2.1.tgz
```

## v2.2.0 - Multimodal-First Knowledge OS Release

Lattice AI v2.2.0 reframes the product as an AI Knowledge Graph workspace. The release moves
model policy, documentation, UI copy, and recommendation logic toward a
multimodal-first Knowledge Graph architecture.

### Highlights

- README and architecture docs rewritten around AI Knowledge Graph workspace direction.
- New principle docs added for AI philosophy, model policy, and Knowledge Graph
  behavior.
- Local model catalogs now recommend current multimodal families only.
- Gemma 4 is the default recommendation family.
- Gemma 2, Gemma 3, Qwen2.5-VL, text-only fallback models, and MLX-LM
  recommendation paths are removed.
- Model entries now carry source disclosure metadata.
- Basic and advanced modes remain feature-equivalent; admin mode carries the
  actual authority boundary.
- Version metadata is aligned to `2.2.0`.

### Expected Artifacts

```text
dist/ltcai-2.2.0-py3-none-any.whl
dist/ltcai-2.2.0.tar.gz
dist/ltcai-2.2.0.vsix
ltcai-2.2.0.tgz
```
