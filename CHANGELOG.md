# Changelog

The detailed historical changelog lives in [docs/CHANGELOG.md](docs/CHANGELOG.md).

## [2.2.6] - 2026-06-05

Token-native CSS foundation. Eliminates the root cause of foggy/washed-out dark
mode instead of patching it with loaded-last overrides.

- **Legacy monolith removed** — the 7,985-line `static/lattice-reference.css` is
  split into token-native modules under `static/css/reference/`
  (`base`/`account`/`admin`/`graph`/`chat`) and deleted. The split is byte-for-byte
  concatenation-equivalent to the original cascade, so no layout regressed.
- **Dark mode fixed at source** — the chat skin no longer redefines color tokens
  with light literals; it is gated to the light theme, so dark mode inherits the
  `tokens.css` dark palette. Every active surface (canvas, sidebar, header, chat
  input, cards, modals, drawers, admin/VPC panels, onboarding) now uses
  `var(--token)`. ~180 hardcoded `#fff`/`white`/`rgba(255,255,255,…)`/lavender
  literals were converted to tokens; status/brand colors flip via semantic tokens.
- **Loading-order dependency gone** — the `:root[data-lt-theme="dark"]` override
  stack in `responsive.css` (~360 lines) is removed; theme correctness no longer
  depends on which stylesheet loads last. Active `!important` declarations: 0.
- **No full-screen blur / white scrims** — `backdrop-filter: blur` removed from
  full-screen overlays; page backgrounds route through the theme-aware `--app-bg`.
- **Stronger visual tests** — new `tests/visual/v226.spec.js` data-drives dark +
  light scans across account/admin/graph/chat (+ mobile, overlays open): no
  opaque-light surface in dark, WCAG text-contrast ≥ 2.2 in both themes, and no
  full-page backdrop blur.

## [2.2.5] - 2026-06-04

Release hygiene hotfix for dark-mode overlays, modal state, cache-busting,
favicon serving, and Telegram token logging.

- **Overlay root cause** — multiple full-screen overlays mixed translucent light
  violet/white backdrops with `backdrop-filter`, which made dark mode look
  foggy and washed out. Overlay backdrops now resolve through `--overlay-scrim`
  and disable blur for crisp dark contrast.
- **Modal state** — Chat overlays now go through a shared modal manager that
  enforces one blocking modal at a time, handles Escape and backdrop dismissal,
  clears stale overlays on navigation/pagehide, and restores body scroll lock.
- **Dark token cleanup** — added `--surface-muted`, `--surface-elevated`,
  `--text-muted`, `--overlay-scrim`, and input aliases, then mapped modal,
  drawer, local file, onboarding, model, pipeline, admin, and My Computer
  surfaces back to semantic tokens.
- **Release hygiene** — all versioned static assets now use `?v=2.2.5`;
  `/static/scripts/chat.js` is loaded as `/static/scripts/chat.js?v=2.2.5`.
- **Favicon** — added `static/favicon.ico` and a `/favicon.ico` route.
- **Telegram logging** — added token redaction so `bot123:secret` is logged as
  `bot123:REDACTED` across Telegram HTTP/exception logging.
- **Tests** — added unit tests for token masking, favicon/static asset hygiene,
  and Playwright coverage for modal stack/scroll-lock/permission restore.

## [2.2.4] - 2026-06-04

Chat dark-mode completion. Resolves the v2.2.3 known issue where the entire Chat
page rendered light in dark mode. All fixes keep the design-token system, add no
`!important`, use no inline-style band-aids, and do not regress light mode.

- **Root cause** — `body.lattice-ref-chat` (in `lattice-reference.css`) redefines
  16 color tokens (`--bg`, `--surface`, `--text`, `--border`, …) as *light
  literals*. Because `<body>` is a descendant of `:root`, those body-level values
  shadowed the `:root[data-lt-theme="dark"]` tokens, so every `var(--token)` on
  the chat page resolved light even in dark mode.
- **Fix** — added a `:root[data-lt-theme="dark"] body.lattice-ref-chat` block that
  re-points the same 16 tokens to the `tokens.css` dark palette (a proper theme
  branch, not a specificity trick), so all token-driven chat surfaces (body,
  bubbles, sidebar, header, composer, …) switch to dark at once.
- **Hardcoded-light surfaces** — surfaces that baked in light colors instead of
  tokens (sidebar, user strip, chat header, mode segmented control, logout/lang
  button, account/MCP/mode/workspace modals, modal inputs, composer box,
  workspace cards) were corrected with `[data-lt-theme="dark"]`-scoped overrides
  mapped to dark tokens (`--sidebar`, `--modal`, `--input`, …). Identified via a
  data-driven scan that walks every visible element for opaque-light backgrounds.
- **Toast** — moved off an inline `cssText` with hardcoded light colors to a
  token-driven `#ltcai-toast` CSS rule, so it adapts to light/dark.
- **Tests** — new `tests/visual/v224.spec.js` (14 tests): a zero-light-surface
  dark scan across shell + overlays + bubbles, dark surface/text checks, a
  light-mode non-regression guard, theme-aware toast, and 10-width responsive
  (375→3440) horizontal-scroll / composer-clip checks. Visual suite now 52 tests.
- Asset cache-busting bumped to `?v=2.2.4`.

## [2.2.3] - 2026-06-04

Frontend Stability & UX Fixes. A no-features stabilization release that fixes
real usability regressions found after v2.2.1 and hardens the UI. All fixes keep
the existing design-token system, add no `!important`, and avoid
specificity-only overrides.

- **Login input readability (dark theme)** — on the login screen the input field
  backgrounds were hardcoded light while the input text used a theme token that
  flips to near-white in dark mode, so email/password text was invisible
  ("white on white"). Added a proper `[data-lt-theme="dark"]`-scoped auth theme
  (dark glass card/titlebar/fields, light title/subtitle, dark SSO buttons) plus
  a Chrome/Safari autofill correction. Light theme is unchanged.
- **Recommendation result not clickable / not scrollable** — the onboarding
  recommendation step put its content (the Gemma 4 / Qwen3-VL / Llama 4 model
  accordions and the action buttons) in `#onboarding-body`, but the rule meant
  to make it scroll used a compound selector (`.onboarding-body.lattice-ref-chat`)
  that never matched, so the body had no scroll region and the card clipped it.
  Long content and the bottom buttons were unreachable. Fixed the scroll region;
  the accordions and actions are now reachable and clickable.
- **Recommendation dark readability** — the onboarding card and its inner cards
  were hardcoded light; re-pointed the overlay's tokens to the dark palette and
  tokenized the "Best for this PC" callout so the screen is readable in dark.
- **Button interaction stability** — verified clickability/hit-testing for login,
  onboarding, graph, and admin controls (no overlay/`pointer-events`/`z-index`
  blockers).
- **Playwright QA strengthened** — added login readability (light/dark),
  recommendation scroll + accordion + reachable-actions, dark-mode readability,
  and uncaught-page-error tests; total visual suite now 38 tests.
- Asset cache-busting query strings bumped to `?v=2.2.3`.

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
