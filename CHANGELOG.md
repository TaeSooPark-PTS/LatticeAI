# Changelog

The detailed historical changelog lives in [docs/CHANGELOG.md](docs/CHANGELOG.md).

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

## [2.2.0] - 2026-06-04

- Reframed Lattice AI as a multimodal-first AI Knowledge OS.
- Removed current text-only local model recommendations and MLX-LM execution
  recommendation paths.
- Removed current Gemma 2, Gemma 3, Qwen2.5-VL, GPT-OSS, Phi, Mistral,
  DeepSeek, SmolLM, and Llama 3.x local recommendation entries.
- Added source disclosure metadata to recommended model catalog entries.
- Updated README, architecture, release notes, and model/graph policy docs.
- Bumped Python, npm, VS Code extension, and runtime version metadata to 2.2.0.

