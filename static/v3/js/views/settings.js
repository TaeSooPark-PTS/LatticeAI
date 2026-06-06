/* ============================================================================
 * View: Settings — appearance, workspace, and integration readiness.
 * This view WIRES real store state (theme + mode persist immediately) and
 * probes the documented future endpoints so the v3 shell visibly reports
 * whether it is talking to a live backend or clearly-badged sample data.
 * ========================================================================== */

const MODE_DEFS = [
  { key: "basic", label: "Basic", desc: "Chat, search, and files — the essentials, nothing else." },
  { key: "advanced", label: "Advanced", desc: "Adds the pipeline, agents, and model runtime surfaces." },
  { key: "admin", label: "Admin", desc: "Reveals users, permissions, audit, security, and policies." },
];

// Endpoints the views light up against once the backend exposes them.
const PROBES = [
  { path: "/api/index/status", method: "GET", call: (api) => api.indexStatus() },
  { path: "/api/graph", method: "GET", call: (api) => api.graph() },
  { path: "/api/search/hybrid", method: "POST", call: (api) => api.hybridSearch("ping") },
];

export async function render(ctx) {
  const { h, icon, api, store, c, navigate, toast } = ctx;

  const probesHost = h("div", c.loading({ lines: 3 }));

  const root = h("div.lt3-stack-6",
    c.viewHeader({
      eyebrow: "System",
      title: "Settings",
      sub: "Appearance, workspace, and integrations.",
    }),

    appearancePanel(ctx),
    workspacePanel(ctx),

    c.panel({
      eyebrow: "Status",
      title: "Integration readiness",
      sub: "Each view probes its endpoint and falls back to sample data until the backend answers. Live the moment these surfaces exist — no view changes required.",
      children: h("div.lt3-stack-3",
        probesHost,
        h("p.lt3-faint", { style: { "font-size": "var(--lt3-text-xs)" } },
          "Views automatically switch to live data once these endpoints respond — the adapter prefers the real endpoint and only labels sample data when it is unreachable."),
      ),
    }),

    aboutPanel(ctx),
  );

  probeEndpoints(ctx, probesHost);
  return root;
}

/* ── Appearance ─────────────────────────────────────────────────────────── */
function appearancePanel({ h, icon, store, c }) {
  const themeKey = () => {
    const t = store.get().theme;
    return t === "light" || t === "dark" ? t : "";
  };

  const themeSlot = h("div");
  const buildTheme = () => c.segmented(
    [{ key: "light", label: "Light" }, { key: "dark", label: "Dark" }, { key: "", label: "System" }],
    themeKey(),
    (k) => { store.setTheme(k); themeSlot.replaceChildren(buildTheme()); },
  );
  themeSlot.append(buildTheme());

  const modeSeg = c.segmented(
    MODE_DEFS.map((m) => ({ key: m.key, label: m.label })),
    store.get().mode,
    (k) => { store.setMode(k); modeNote.replaceChildren(noteFor(k)); },
  );
  const noteFor = (k) => h("span", (MODE_DEFS.find((m) => m.key === k) || MODE_DEFS[0]).desc);
  const modeNote = h("p.lt3-faint", { style: { "font-size": "var(--lt3-text-xs)" } }, noteFor(store.get().mode));

  return c.panel({
    eyebrow: "Appearance",
    title: "Look and density",
    sub: "Theme and surface mode persist on this machine and apply across every view.",
    children: h("div.lt3-stack-6",
      h("div.lt3-field",
        h("label.lt3-label", { style: { "display": "flex", "gap": "var(--lt3-space-2)", "align-items": "center" } }, icon("palette"), "Theme"),
        themeSlot,
        h("span.lt3-faint", { style: { "font-size": "var(--lt3-text-xs)" } }, "System follows your OS appearance preference."),
      ),
      h("div.lt3-field",
        h("label.lt3-label", { style: { "display": "flex", "gap": "var(--lt3-space-2)", "align-items": "center" } }, icon("adjustments"), "Mode"),
        h("div", modeSeg),
        modeNote,
      ),
    ),
  });
}

/* ── Workspace ──────────────────────────────────────────────────────────── */
function workspacePanel({ h, icon, store, c, toast }) {
  const ws = store.activeWorkspace();

  const orgInput = h("input.lt3-input", {
    type: "text", placeholder: "Organization name…", "aria-label": "New organization name",
    style: { "flex": "1 1 220px" },
  });
  const createOrg = () => {
    const name = (orgInput.value || "").trim();
    toast(name
      ? `“${name}” — organization workspaces are provisioned by the backend (pending).`
      : "Organization workspaces are provisioned by the backend — pending.", "info");
  };

  const langSelect = h("select.lt3-select", {
    "aria-label": "Interface language",
    on: { change: (e) => toast(`Language preference (${e.target.selectedOptions[0].text}) will persist once the backend stores it — pending.`, "info") },
  },
    h("option", { value: "en" }, "English"),
    h("option", { value: "ko" }, "한국어"),
  );

  return c.panel({
    eyebrow: "Workspace",
    title: "Active workspace",
    sub: "Where your indexed knowledge, agents, and policies live.",
    children: h("div.lt3-stack-6",
      h("dl.lt3-keyval",
        h("dt", "Name"), h("dd", ws.name),
        h("dt", "Type"), h("dd", h("span.lt3-row-2", icon(ws.type === "personal" ? "user" : "building"), titleCase(ws.type || "personal"))),
        h("dt", "Your role"), h("dd", c.pill(titleCase(ws.your_role || "owner"), "info")),
      ),
      h("hr.lt3-divider"),
      h("div.lt3-field",
        h("label.lt3-label", { style: { "display": "flex", "gap": "var(--lt3-space-2)", "align-items": "center" } }, icon("building-community"), "Create organization"),
        h("div.lt3-cluster",
          orgInput,
          h("button.lt3-btn.lt3-btn--primary", { type: "button", on: { click: createOrg } }, icon("plus"), "Create organization"),
        ),
        h("span.lt3-faint", { style: { "font-size": "var(--lt3-text-xs)" } }, "Shared organization workspaces require the backend — coming soon."),
      ),
      h("div.lt3-field",
        h("label.lt3-label", { for: "lt3-set-lang", style: { "display": "flex", "gap": "var(--lt3-space-2)", "align-items": "center" } }, icon("language"), "Language"),
        h("div", { style: { "max-width": "260px" } }, langSelect),
      ),
    ),
  });
}

/* ── Integration readiness ──────────────────────────────────────────────── */
async function probeEndpoints({ h, icon, api, c }, host) {
  const results = await Promise.all(PROBES.map((p) => p.call(api)));
  const rows = PROBES.map((p, i) => {
    const res = results[i] || {};
    return h("div.lt3-card.lt3-card--flat",
      h("div.lt3-row", { style: { "justify-content": "space-between", "gap": "var(--lt3-space-3)", "flex-wrap": "wrap" } },
        h("div.lt3-row-2",
          h("span.lt3-pill", { style: { "font-weight": "var(--lt3-weight-medium)" } }, p.method),
          h("code.lt3-mono", p.path),
        ),
        c.sourceBadge(res.source === "live" ? "live" : "placeholder"),
      ),
    );
  });
  host.replaceChildren(h("div.lt3-stack-2", rows));
}

/* ── About ──────────────────────────────────────────────────────────────── */
function aboutPanel({ h, icon, c }) {
  return c.panel({
    eyebrow: "About",
    title: "Lattice AI",
    sub: "Local-first AI workspace.",
    children: h("div.lt3-stack-4",
      h("dl.lt3-keyval",
        h("dt", "Application"), h("dd", "Lattice AI"),
        h("dt", "Version"), h("dd", h("span.lt3-mono", "v3.0.0")),
        h("dt", "Edition"), h("dd", "Local-first AI workspace"),
      ),
      h("hr.lt3-divider"),
      h("div.lt3-cluster",
        h("button.lt3-btn.lt3-btn--ghost", {
          type: "button",
          on: { click: () => { window.location.href = "/workspace"; } },
        }, icon("layout-dashboard"), "Open classic workspace"),
        h("span.lt3-faint", { style: { "font-size": "var(--lt3-text-xs)" } }, "The original Lattice surface remains available."),
      ),
    ),
  });
}

/* ── helpers ────────────────────────────────────────────────────────────── */
function titleCase(s) {
  s = String(s || "");
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
}
