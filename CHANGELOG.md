# Changelog

The detailed historical changelog lives in [docs/CHANGELOG.md](docs/CHANGELOG.md).

## [2.2.2] - 2026-06-04

Frontend QA stabilization release. No new features — this hardens the v2.2.x
UI and finalizes release documentation. Fixes were made at the source/override
layer with no `!important` and no change to the design-token structure.

- Fixed the mobile hamburger menus on the Knowledge Graph and Admin pages: the
  default-hidden rule for `.graph-nav-toggle` / `.admin-rail-toggle` was declared
  *after* its reveal, so source order kept the toggles hidden on phones and
  tablets and the navigation drawers were unreachable.
- Fixed Admin top-bar actions (Refresh, Logout) being unclickable: a
  graph-only `.toolbar { position: absolute; z-index: 20 }` rule had an
  over-broad selector that leaked onto the Admin/Chat form toolbars, floating
  them over the header and intercepting clicks. The toolbars are now scoped back
  to normal flow off the graph page.
- Fixed latent horizontal overflow on the Workspace page caused by a
  visually-hidden checkbox (`#computer-memory-toggle`) that stretched to the
  viewport width; it is now constrained to a 1px hit-box.
- Verified light/dark theme parity, no-horizontal-scroll, button hit-testing,
  mobile drawer open/close, and Escape-to-close across the full viewport matrix
  (375px phone → 3440px ultrawide) with an expanded Playwright suite.
- README finalized as a product landing page; asset cache-busting query strings
  bumped so the QA fixes ship to existing installs.

## [2.2.1] - 2026-06-04

- Shipped a mobile-first responsive UI across phone, tablet, laptop, desktop,
  ultrawide, and 4K. No content is hidden on smaller screens; it is only
  re-laid out.
- Added light/dark mode with OS detection, a manual toggle, and persistence.
- Rebuilt the design-token system as a single source of truth
  (`static/css/tokens.css`); theming no longer relies on `!important`.
- Improved accessibility: 44px touch targets, `:focus-visible` rings, a
  keyboard-safe chat composer (visualViewport inset), iOS no-zoom inputs, and
  reduced-motion support.
- Reworked the Knowledge Graph UX with a responsive canvas that re-fits on
  resize, zoom buttons, fullscreen, a minimap, a relationship filter, a mobile
  graph/card view, and a theme-aware palette.
- Reflowed wide admin tables into cards on mobile, with responsive layout,
  dark/light support, and larger touch targets.
- Added file attach by drag & drop and screenshot paste.
- Model cards now describe country, company, run mode, and internet usage in
  plain language.
- README, marketplace metadata, and release copy now present Lattice AI as a
  local-first AI workspace for knowledge graphs, AI pipelines, model workflows,
  and multi-agent coding workflows.

## [2.2.0] - 2026-06-04

- Reframed Lattice AI as a multimodal-first AI Knowledge Graph workspace.
- Removed current text-only local model recommendations and MLX-LM execution
  recommendation paths.
- Removed current Gemma 2, Gemma 3, Qwen2.5-VL, GPT-OSS, Phi, Mistral,
  DeepSeek, SmolLM, and Llama 3.x local recommendation entries.
- Added source disclosure metadata to recommended model catalog entries.
- Updated README, architecture, release notes, and model/graph policy docs.
- Bumped Python, npm, VS Code extension, and runtime version metadata to 2.2.0.
