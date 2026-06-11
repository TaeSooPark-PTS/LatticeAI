/* ============================================================================
 * Lattice AI v3 — App state store
 * A minimal observable store with namespaced persistence. Holds the cross-view
 * product state: theme, mode (Basic/Advanced/Admin), and the active workspace
 * (Personal/Organization). Views/shell subscribe to react to changes.
 * ========================================================================== */

const LS = {
  theme: "lt-theme",        // shared with the rest of Lattice (data-lt-theme)
  mode: "lt3-mode",
  workspace: "lt3-workspace",
};

function load(key, fallback) {
  try { const v = localStorage.getItem(key); return v == null ? fallback : v; }
  catch { return fallback; }
}
function save(key, value) {
  try { localStorage.setItem(key, value); } catch { /* private mode */ }
}

const VALID_MODES = ["basic", "advanced", "admin"];

const state = {
  theme: load(LS.theme, ""),                 // "" → follow OS
  mode: VALID_MODES.includes(load(LS.mode)) ? load(LS.mode) : "basic",
  workspaceId: load(LS.workspace, "personal"),
  workspaces: [
    { workspace_id: "personal", name: "Personal Workspace", type: "personal", your_role: "owner" },
  ],
  user: { email: "", nickname: "You", role: "user" },
  indexStatus: null,
  route: { key: "knowledge-graph", params: {} },
};

const subscribers = new Set();

function emit(change) {
  for (const fn of subscribers) {
    try { fn(state, change); } catch (err) { console.error("[store] subscriber", err); }
  }
}

export const store = {
  get: () => state,

  subscribe(fn) { subscribers.add(fn); return () => subscribers.delete(fn); },

  /* ── Theme ─────────────────────────────────────────────── */
  applyTheme() {
    const root = document.documentElement;
    if (state.theme === "dark" || state.theme === "light") {
      root.setAttribute("data-lt-theme", state.theme);
    } else {
      root.removeAttribute("data-lt-theme"); // OS-follow via tokens.css media query
    }
  },
  setTheme(theme) {
    state.theme = theme === "dark" || theme === "light" ? theme : "";
    if (state.theme) save(LS.theme, state.theme); else { try { localStorage.removeItem(LS.theme); } catch {} }
    store.applyTheme();
    emit({ type: "theme" });
  },
  toggleTheme() {
    const effective = document.documentElement.getAttribute("data-lt-theme")
      || (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    store.setTheme(effective === "dark" ? "light" : "dark");
  },

  /* ── Mode ──────────────────────────────────────────────── */
  setMode(mode) {
    if (!VALID_MODES.includes(mode) || mode === state.mode) return;
    state.mode = mode;
    save(LS.mode, mode);
    emit({ type: "mode" });
  },

  /* ── Workspace ─────────────────────────────────────────── */
  setWorkspaces(list) {
    if (Array.isArray(list) && list.length) {
      state.workspaces = list;
      if (!list.some((w) => w.workspace_id === state.workspaceId)) {
        state.workspaceId = list[0].workspace_id;
        save(LS.workspace, state.workspaceId);
      }
      emit({ type: "workspaces" });
    }
  },
  setWorkspace(id) {
    if (id === state.workspaceId) return;
    state.workspaceId = id;
    save(LS.workspace, id);
    emit({ type: "workspace" });
  },
  activeWorkspace() {
    return state.workspaces.find((w) => w.workspace_id === state.workspaceId) || state.workspaces[0];
  },

  setUser(user) { state.user = { ...state.user, ...user }; emit({ type: "user" }); },
  setIndexStatus(s) { state.indexStatus = s; emit({ type: "index" }); },
  setRoute(route) { state.route = route; emit({ type: "route" }); },
};

store.applyTheme();

// Follow OS theme changes while the user hasn't pinned a preference.
if (window.matchMedia) {
  try {
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
      if (!state.theme) emit({ type: "theme" });
    });
  } catch { /* legacy Safari */ }
}
