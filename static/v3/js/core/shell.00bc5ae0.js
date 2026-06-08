/* ============================================================================
 * Lattice AI v3 — Application shell
 * Builds the persistent chrome (nav rail, topbar, view outlet), wires the
 * router, workspace + mode + theme switchers, command palette, and mobile
 * drawer. Renders views by lazy-loading their module and calling render(ctx).
 * ========================================================================== */

import { h, icon, $, $$ } from "./dom.a2773eb0.js";
import { store } from "./store.34ebd5e6.js";
import { api } from "./api.113660c5.js";
import * as c from "./components.4c83e0a9.js";
import { createRouter } from "./router.584570f2.js";
import { GROUPS, ROUTES, ROUTE_BY_KEY, MODE_RANK, visibleRoutes, loadView } from "./routes.d214b399.js";

const MODES = [
  { key: "basic", label: "Basic", icon: "circle" },
  { key: "advanced", label: "Advanced", icon: "circles" },
  { key: "admin", label: "Admin", icon: "shield-half" },
];

const ctxBase = { h, icon, api, store, c };

let els = {};
let router;
let currentRoute = null;

export function boot(rootEl) {
  rootEl.classList.add("lt3-app");
  rootEl.append(
    h("a.lt3-skip", { href: "#lt3-view" }, "Skip to content"),
    h("div.lt3-rail__scrim", { on: { click: closeDrawer } }),
    buildRail(),
    buildMain(),
  );

  cacheEls(rootEl);
  store.subscribe(onStateChange);

  router = createRouter({ onRoute: renderRoute, fallback: "home" });
  wireGlobalKeys();
  router.start();

  // Background: hydrate workspaces, identity, index status.
  hydrate();
}

/* ── Rail ────────────────────────────────────────────────────────────────── */
function buildRail() {
  return h("aside.lt3-rail", { id: "lt3-rail", "aria-label": "Primary" },
    h("div.lt3-rail__brand",
      h("div.lt3-rail__logo", { html: latticeMark() }),
      h("div.lt3-rail__word", h("b", "Lattice AI"), h("small", "Private runtime")),
      h("button.lt3-iconbtn.lt3-iconbtn--sm.lt3-rail__close", { "aria-label": "Close menu", on: { click: closeDrawer } }, icon("x")),
    ),
    h("div.lt3-rail__scope", { id: "lt3-scope" }),
    h("nav.lt3-rail__nav", { id: "lt3-nav", "aria-label": "Sections" }),
    h("div.lt3-rail__foot",
      h("div.lt3-rail__status", { id: "lt3-rail-status" }),
      h("div.lt3-rail__foot-row",
        h("button.lt3-rail__user", { id: "lt3-user", "aria-label": "Account", on: { click: () => router.navigate("settings") } }),
        h("button.lt3-iconbtn", { id: "lt3-theme", "aria-label": "Toggle theme", title: "Toggle theme", on: { click: () => store.toggleTheme() } }, icon("moon")),
      ),
    ),
  );
}

function renderNav() {
  const nav = els.nav;
  const mode = store.get().mode;
  const routes = visibleRoutes(mode);
  nav.replaceChildren();
  for (const group of GROUPS) {
    const items = routes.filter((r) => r.group === group.id);
    if (!items.length) continue;
    const groupEl = h("div.lt3-navgroup",
      h("div.lt3-navgroup__label", group.label),
      items.map((r) => navItem(r)),
    );
    nav.append(groupEl);
  }
  markActive();
}

function navItem(route) {
  return h("a.lt3-navitem", {
    href: "#/" + route.key,
    dataset: { key: route.key },
    title: route.title || route.label,
    on: { click: () => closeDrawer() },
  },
    icon(route.icon),
    h("span.lt3-navitem__copy",
      h("span.lt3-navitem__label", route.label),
      route.desc ? h("span.lt3-navitem__meta", route.desc) : null,
    ),
    route.key === "hybrid-search" ? h("span.lt3-navitem__dot", { style: { background: "var(--lt3-pillar-hybrid)" } }) : null,
  );
}

function markActive() {
  $$(".lt3-navitem", els.nav).forEach((a) => {
    const on = a.dataset.key === (currentRoute && currentRoute.key);
    if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
}

function renderScope() {
  const ws = store.activeWorkspace();
  els.scope.replaceChildren(
    h("button.lt3-scope", { "aria-haspopup": "listbox", on: { click: openScopeMenu } },
      h("div.lt3-scope__icon", icon(ws.type === "organization" ? "building-community" : "user")),
      h("div.lt3-scope__meta", h("b", ws.name), h("small", `${ws.type} · ${ws.your_role || "member"}`)),
      icon("selector"),
    ),
  );
}

function renderUser() {
  const u = store.get().user;
  const initials = (u.nickname || u.email || "U").slice(0, 2);
  els.user.replaceChildren(
    h("span.lt3-avatar", initials),
    h("div.lt3-rail__user-meta", h("b", u.nickname || u.email || "You"), h("small", u.role || "local")),
  );
}

function updateThemeIcon() {
  const dark = document.documentElement.getAttribute("data-lt-theme") === "dark"
    || (!store.get().theme && window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
  els.theme.replaceChildren(icon(dark ? "sun" : "moon"));
}

/* ── Main / topbar ──────────────────────────────────────────────────────── */
function buildMain() {
  return h("div.lt3-main",
    h("header.lt3-topbar",
      h("button.lt3-iconbtn.lt3-topbar__menu", { "aria-label": "Open menu", on: { click: openDrawer } }, icon("menu-2")),
      h("div.lt3-topbar__crumbs", { id: "lt3-crumbs" }),
      h("div.lt3-spacer"),
      h("button.lt3-cmd-trigger", { "aria-label": "Search and commands", on: { click: openPalette } },
        icon("search"), h("span", "Search & commands"), h("span.lt3-kbd", "⌘K")),
      h("div", { id: "lt3-idxchip" }),
      h("div.lt3-mode", { id: "lt3-mode", role: "tablist", "aria-label": "Workspace mode" },
        MODES.map((m) => h("button", {
          type: "button", role: "tab", dataset: { mode: m.key },
          on: { click: () => store.setMode(m.key) },
        }, icon(m.icon), h("span", m.label))),
      ),
    ),
    h("main.lt3-view", { id: "lt3-view", tabindex: "-1" },
      h("div.lt3-view__inner", { id: "lt3-outlet" }),
    ),
  );
}

function renderMode() {
  $$("#lt3-mode button", els.root).forEach((b) => b.dataset.active = String(b.dataset.mode === store.get().mode));
}

function renderCrumbs() {
  const r = currentRoute;
  if (!r) return;
  const parts = [h("span.lt3-crumb", store.activeWorkspace().name)];
  if (r.group === "admin") parts.push(icon("chevron-right"), h("span.lt3-crumb", "Admin"));
  parts.push(icon("chevron-right"), h("span.lt3-crumb.lt3-crumb--current", r.title || r.label));
  els.crumbs.replaceChildren(...parts);
}

function renderIndexChip() {
  els.idxchip.replaceChildren(c.indexChip(store.get().indexStatus));
  renderRailStatus();
}

function renderRailStatus() {
  if (!els.railStatus) return;
  const status = store.get().indexStatus;
  const pipes = status?.pipelines || {};
  const keys = ["knowledge_graph", "vector_index", "hybrid"];
  const ready = keys.filter((key) => String(pipes[key]?.state || "").toLowerCase() === "ready").length;
  const unavailable = !Object.keys(pipes).length;
  els.railStatus.replaceChildren(
    h("div.lt3-rail__status-top",
      h("span.lt3-rail__status-dot", { dataset: { state: unavailable ? "pending" : ready === keys.length ? "ready" : "partial" } }),
      h("span", unavailable ? "Local index pending" : `${ready}/${keys.length} retrieval signals ready`),
    ),
    h("div.lt3-rail__status-sub", unavailable ? "Start backend to sync live state" : "Graph · vector · hybrid"),
  );
}

/* ── View rendering ─────────────────────────────────────────────────────── */
async function renderRoute({ key, params }) {
  let route = ROUTE_BY_KEY[key] || ROUTE_BY_KEY.home;
  // Deep-linking into an admin area surfaces Admin mode so the rail matches.
  if (route.admin && store.get().mode !== "admin") store.setMode("admin");
  currentRoute = route;
  store.setRoute({ key: route.key, params });

  document.title = `${route.title || route.label} · Lattice AI`;
  markActive();
  renderCrumbs();

  const outlet = els.outlet;
  outlet.replaceChildren(c.loading({ lines: 4, block: true }));
  els.view.scrollTop = 0;

  try {
    const mod = await loadView(route.view);
    if (currentRoute !== route) return; // navigated away during load
    els.view.classList.toggle("lt3-view--flush", mod.layout === "flush");
    const ctx = { ...ctxBase, route, params, navigate: router.navigate, toast: c.toast };
    const node = await mod.render(ctx);
    if (currentRoute !== route) return;
    outlet.replaceChildren(node);
  } catch (err) {
    console.error("[shell] view render failed:", route.view, err);
    outlet.replaceChildren(c.errorState(`View "${route.label}" failed to load.`, () => renderRoute({ key: route.key, params })));
  }
}

function renderCurrent() {
  if (currentRoute) renderRoute({ key: currentRoute.key, params: store.get().route.params || {} });
}

/* ── State reactions ────────────────────────────────────────────────────── */
function onStateChange(_state, change) {
  switch (change.type) {
    case "mode": renderNav(); renderMode(); renderCurrent(); break;
    case "workspace": renderScope(); renderCrumbs(); renderCurrent(); break;
    case "workspaces": renderScope(); break;
    case "user": renderUser(); break;
    case "theme": updateThemeIcon(); break;
    case "index": renderIndexChip(); break;
  }
}

/* ── Workspace scope menu ───────────────────────────────────────────────── */
function openScopeMenu(ev) {
  ev.stopPropagation();
  closeMenus();
  const rect = ev.currentTarget.getBoundingClientRect();
  const list = store.get().workspaces;
  const menu = h("div.lt3-menu", { id: "lt3-scope-menu", role: "listbox", style: { top: rect.bottom + 6 + "px", left: rect.left + "px" } },
    list.map((w) => h("button.lt3-menu__item", {
      role: "option", dataset: { active: String(w.workspace_id === store.get().workspaceId) },
      on: { click: () => { store.setWorkspace(w.workspace_id); closeMenus(); } },
    },
      icon(w.type === "organization" ? "building-community" : "user"),
      h("div", h("div", { style: { fontWeight: 600 } }, w.name), h("small.lt3-faint", { style: { textTransform: "capitalize" } }, w.type)),
      w.workspace_id === store.get().workspaceId ? icon("check", "") : null,
    )),
    h("div.lt3-menu__sep"),
    h("button.lt3-menu__item", { on: { click: () => { c.toast("Organization creation opens in Settings", "info"); closeMenus(); router.navigate("settings"); } } },
      icon("plus"), "New organization"),
  );
  document.body.append(menu);
  setTimeout(() => document.addEventListener("click", closeMenusOnce, { once: true }), 0);
}
function closeMenus() { $$(".lt3-menu").forEach((m) => m.remove()); }
function closeMenusOnce() { closeMenus(); }

/* ── Mobile drawer ──────────────────────────────────────────────────────── */
function openDrawer() { els.root.dataset.drawer = "open"; }
function closeDrawer() { delete els.root.dataset.drawer; }

/* ── Command palette ────────────────────────────────────────────────────── */
function paletteItems() {
  const mode = store.get().mode;
  const currentRoutes = visibleRoutes(mode);
  const nav = currentRoutes.map((r) => ({
    group: "Go to", label: r.title || r.label, icon: r.icon, hint: r.label === r.title ? r.group : r.label,
    run: () => router.navigate(r.key),
  }));
  const actions = [
    { group: "Actions", label: "Toggle light / dark theme", icon: "contrast", run: () => store.toggleTheme() },
    { group: "Actions", label: "Mode: Basic", icon: "circle", run: () => store.setMode("basic") },
    { group: "Actions", label: "Mode: Advanced", icon: "circles", run: () => store.setMode("advanced") },
    { group: "Actions", label: "Mode: Admin", icon: "shield-half", run: () => store.setMode("admin") },
    { group: "Actions", label: "New chat", icon: "message-plus", run: () => router.navigate("chat", { new: "1" }) },
    { group: "Actions", label: "Run hybrid search", icon: "arrows-join", run: () => router.navigate("hybrid-search") },
  ];
  return [...nav, ...actions];
}

function openPalette() {
  if ($("#lt3-palette")) return;
  const all = paletteItems();
  let active = 0, filtered = all;

  const listEl = h("div.lt3-palette__list");
  const input = h("input", { type: "text", placeholder: "Search views, run a command…", "aria-label": "Command palette", autocomplete: "off" });
  const palette = h("div.lt3-palette", { id: "lt3-palette", role: "dialog", "aria-modal": "true", "aria-label": "Command palette" },
    h("div.lt3-palette__input", icon("search"), input, h("span.lt3-kbd", "Esc")),
    listEl,
  );
  const scrim = h("div.lt3-scrim", { id: "lt3-palette-scrim", on: { click: close } });
  document.body.append(scrim, palette);
  input.focus();

  function renderList() {
    listEl.replaceChildren();
    if (!filtered.length) { listEl.append(h("div.lt3-palette__empty", "No matches")); return; }
    let lastGroup = null;
    filtered.forEach((item, i) => {
      if (item.group !== lastGroup) { listEl.append(h("div.lt3-palette__group-label", item.group)); lastGroup = item.group; }
      listEl.append(h("button.lt3-palette__item", {
        dataset: { active: String(i === active) },
        on: { click: () => { item.run(); close(); }, mousemove: () => { if (active !== i) { active = i; paint(); } } },
      }, icon(item.icon), h("span", item.label), item.hint && h("small", item.hint)));
    });
  }
  function paint() { $$(".lt3-palette__item", listEl).forEach((el, i) => el.dataset.active = String(i === active)); ensureVisible(); }
  function ensureVisible() {
    const el = $$(".lt3-palette__item", listEl)[active];
    if (el) el.scrollIntoView({ block: "nearest" });
  }
  function filter() {
    const q = input.value.trim().toLowerCase();
    filtered = !q ? all : all.filter((it) => (it.label + " " + (it.hint || "")).toLowerCase().includes(q));
    active = 0; renderList();
  }
  function close() { palette.remove(); scrim.remove(); document.removeEventListener("keydown", onKey, true); }
  function onKey(e) {
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); active = Math.min(filtered.length - 1, active + 1); paint(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); active = Math.max(0, active - 1); paint(); }
    else if (e.key === "Enter") { e.preventDefault(); const it = filtered[active]; if (it) { it.run(); close(); } }
  }
  input.addEventListener("input", filter);
  document.addEventListener("keydown", onKey, true);
  renderList();
}

function wireGlobalKeys() {
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && (e.key === "k" || e.key === "K")) { e.preventDefault(); openPalette(); }
    if (e.key === "Escape") { closeMenus(); closeDrawer(); }
  });
}

/* ── Hydration ──────────────────────────────────────────────────────────── */
async function hydrate() {
  // Identity (best-effort; never blocks the UI).
  api.raw("/account/profile").then((r) => {
    if (r.ok && r.data && (r.data.email || r.data.nickname)) {
      store.setUser({ email: r.data.email, nickname: r.data.nickname || r.data.email, role: r.data.role || "user" });
    } else { renderUser(); }
  });

  // Workspaces from the OS payload (fallback-safe).
  api.workspaceOs().then((r) => {
    const reg = r.data && r.data.workspace_registry;
    if (reg && Array.isArray(reg.workspaces) && reg.workspaces.length) store.setWorkspaces(reg.workspaces);
  });

  // Index status powers the topbar chip + Home pillars.
  api.indexStatus().then((r) => store.setIndexStatus(r.data));
}

/* ── Init helpers ───────────────────────────────────────────────────────── */
function cacheEls(root) {
  els = {
    root,
    nav: $("#lt3-nav", root),
    scope: $("#lt3-scope", root),
    user: $("#lt3-user", root),
    theme: $("#lt3-theme", root),
    crumbs: $("#lt3-crumbs", root),
    idxchip: $("#lt3-idxchip", root),
    railStatus: $("#lt3-rail-status", root),
    outlet: $("#lt3-outlet", root),
    view: $("#lt3-view", root),
  };
  renderNav();
  renderScope();
  renderUser();
  renderMode();
  updateThemeIcon();
  renderIndexChip();
  renderRailStatus();
}

function latticeMark() {
  // Crystalline lattice glyph — the product mark.
  return `<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
    <path d="M12 2.5 4 7v10l8 4.5L20 17V7L12 2.5Z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" opacity=".55"/>
    <path d="M12 7.5 7.5 10v4L12 16.5 16.5 14v-4L12 7.5Z" fill="currentColor" opacity=".9"/>
    <circle cx="12" cy="2.5" r="1.3" fill="currentColor"/><circle cx="4" cy="7" r="1.1" fill="currentColor"/>
    <circle cx="20" cy="7" r="1.1" fill="currentColor"/><circle cx="4" cy="17" r="1.1" fill="currentColor"/>
    <circle cx="20" cy="17" r="1.1" fill="currentColor"/><circle cx="12" cy="21.5" r="1.3" fill="currentColor"/>
  </svg>`;
}
