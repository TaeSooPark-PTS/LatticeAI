# Lattice AI v3 — Frontend Product Shell

> A ground-up rebuild of the Lattice AI workspace frontend as a single-page,
> token-native application shell. This is a **frontend-only** surface: it ships
> integration-ready UI for the future retrieval APIs and never implements
> backend search, graph, or vector logic.

Entry point: **`/app`** (served by `latticeai/api/static_routes.py` →
`static/v3/index.html`). The legacy multi-page screens (`/workspace`, `/chat`,
`/graph`, `/admin`, …) remain reachable and unchanged, so there is no product
regression while v3 is on a feature branch.

---

## Product model

| Concept | Surface |
| --- | --- |
| **Personal / Organization workspace** | Scope switcher in the rail (`X-Workspace-Id` header is sent on every adapter call). |
| **Basic / Advanced / Admin mode** | Mode switcher in the topbar. Mode gates which rail items appear and how dense each view is. |
| **Retrieval lattice** | The signature identity: **Knowledge Graph + Vector Index + Hybrid Search**, surfaced on Home and as a live 3-dot index chip in the topbar. |

Mode gating (`MODE_RANK` in `core/routes.js`): Basic ⊂ Advanced ⊂ Admin.
Deep-linking into an `admin/*` route auto-promotes the shell to Admin mode.

---

## Information architecture

One declarative table (`static/v3/js/core/routes.js`) drives the nav rail, the
command palette, the router, breadcrumbs, and lazy view loading.

```
Workspace      Home · Chat
Retrieval      Knowledge Graph · Hybrid Search
Data           Files · Pipeline*
Compute        Agents* · Models · My Computer*
System         Settings
Administration Users · Permissions · Audit Logs · Security · Policies · Private VPC   (admin mode)
```

`*` = revealed in Advanced mode and above. Routes are hash-based
(`#/knowledge-graph`, `#/admin/users`) so the SPA needs no server rewrites.

---

## Design system

Token-native, layered on the existing **single color source**
(`static/css/tokens.css`, `data-lt-theme` light/dark). No legacy override layers
(`responsive.css` / `workspace.css` / `platform.css`) are loaded by v3.

| File | Responsibility |
| --- | --- |
| `css/lattice.tokens.css` | Structural tokens — type scale, spacing (4pt), radii, elevation, motion, z-index, layout rails, pillar accents, lattice mesh. |
| `css/lattice.base.css` | Reset, element defaults, the signature lattice backdrop, a11y utilities. |
| `css/lattice.components.css` | Primitive vocabulary — cards, panels, buttons, inputs, pills, stats, tables, tabs, switches, meters, empty/loading/error states, toasts. |
| `css/lattice.shell.css` | App chrome — rail, topbar, command palette, mobile drawer, retrieval pillars. |
| `css/lattice.views.css` | Bespoke view layouts — hero, graph canvas, search results, chat, files, pipeline, admin. |

**Rule:** component CSS uses only `var(--token)`; no per-component hardcoded
themed colors and no `!important`. Both themes flow from the token values.

**Identity:** a crystalline *lattice* mark, a faint node-and-edge mesh backdrop,
and a three-pillar retrieval motif (graph = blue, vector = teal, hybrid =
magenta). Deliberately **not** a clone of ChatGPT / Claude / Cursor / VS Code /
Notion / Obsidian.

---

## Shell + view contract

`core/shell.js` builds the chrome and renders views. Each view is a lazy ES
module under `js/views/` exporting:

```js
export async function render(ctx) { /* … */ return singleDomNode; }
// optional: export const layout = "flush";  // full-bleed (chat)
```

`ctx = { h, icon, api, store, c, route, params, navigate, toast }`

- `h()` — dependency-free hyperscript (`core/dom.js`); child strings are
  auto-escaped (no injection surface).
- `c` — component factories (`core/components.js`).
- `store` — observable app state (theme / mode / workspace), persisted to
  `localStorage` (`core/store.js`).

---

## Integration readiness

`core/api.js` is the only transport layer. Every call hits the **real** endpoint
first and degrades to a clearly-badged sample payload (`core/fixtures.js`) when
the endpoint is absent — returning `{ ok, status, data, source }` where `source`
is `"live"` or `"placeholder"`. The UI always renders a **Sample data** badge for
placeholder responses, so nothing fake is presented as backend output.

Documented future surfaces wired and ready:

| Adapter | Endpoint | Fallback |
| --- | --- | --- |
| `api.indexStatus()` | `GET /api/index/status` | sample KG/vector/hybrid pipeline state |
| `api.graph(params)` | `GET /api/graph` → `GET /knowledge-graph/graph` | sample mesh |
| `api.hybridSearch(q, opts)` | `POST /api/search/hybrid` | sample fused results |

No backend logic is implemented here — only transport and graceful fallback.

---

## Responsive strategy

- **Desktop (≥1101px):** full 268px rail + content.
- **Tablet (761–1100px):** rail collapses to a 76px icon rail.
- **Mobile (≤760px):** rail becomes an off-canvas drawer (hamburger in the
  topbar); grids collapse to single column; the chat context rail hides.

Plus: `prefers-color-scheme` following, `prefers-reduced-motion` honored, visible
focus rings, skip link, keyboard command palette (⌘K / Ctrl-K).

---

## Validation

- `npm run lint` (extended to cover `static/v3/**` via `scripts/lint_v3.mjs`).
- `npm run test:visual` (`tests/visual/v3.spec.js` against the mock server, which
  serves `/app` and mocks the future API surfaces).
- Browser-rendered smoke checks of every route in light and dark themes.
