/* ============================================================================
 * View: Settings — appearance, workspace, and integration readiness.
 * This view WIRES real store state (theme + mode persist immediately) and
 * probes the documented endpoints so the v3 shell visibly reports whether it
 * is talking to a live backend or an unavailable surface.
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

  const embedHost = h("div", c.loading({ lines: 2 }));

  const root = h("div.lt3-stack-6",
    c.viewHeader({
      eyebrow: "System",
      title: "Settings",
      sub: "Appearance, workspace, and integrations.",
    }),

    appearancePanel(ctx),
    workspacePanel(ctx),

    c.panel({
      eyebrow: "Models",
      title: "Embeddings",
      sub: "The vector signal behind retrieval. Configure the provider with LATTICEAI_EMBEDDING_PROVIDER (hash · mlx · ollama · openai · custom).",
      children: embedHost,
    }),

    c.panel({
      eyebrow: "Status",
      title: "Integration readiness",
      sub: "Each view probes its endpoint and reports unavailable state until the backend answers.",
      children: h("div.lt3-stack-3",
        probesHost,
        h("p.lt3-faint", { style: { "font-size": "var(--lt3-text-xs)" } },
          "Views automatically switch to live data once these endpoints respond; unreachable endpoints are labeled unavailable."),
      ),
    }),

    aboutPanel(ctx),
  );

  probeEndpoints(ctx, probesHost);
  renderEmbeddings(ctx, embedHost);
  return root;
}

/* ── Embeddings (Settings → Models → Embeddings) ────────────────────────── */
export function embeddingStatePill({ h, c }, st) {
  const state = String(st.state || st.grade || "fallback").toLowerCase();
  if (state === "production") return c.pill("Production", "ok");
  if (state === "unavailable") return c.pill("Unavailable", "err");
  return c.pill("Fallback", "warn");
}

async function renderEmbeddings(ctx, host) {
  const { h, c } = ctx;
  const res = await ctx.api.embeddingsStatus();
  const d = res.data || {};
  const lastIndexed = d.last_indexed_at ? new Date(d.last_indexed_at).toLocaleString() : "Never";
  host.replaceChildren(
    h("div.lt3-stack-4",
      h("div.lt3-row", { style: { "justify-content": "space-between", "align-items": "center", "flex-wrap": "wrap", gap: "var(--lt3-space-3)" } },
        h("div.lt3-row-2",
          h("span", { style: { color: "var(--lt3-pillar-vector, var(--accent))", display: "inline-flex" } }, ctx.icon("grid-dots")),
          h("b", { style: { "font-size": "var(--lt3-text-md)" } }, providerLabel(d.active_provider || d.provider)),
        ),
        h("div.lt3-row-2", embeddingStatePill(ctx, d), c.sourceBadge(res.source)),
      ),
      d.fell_back
        ? c.banner(`Requested “${d.requested_provider}” is unavailable (${(d.health && d.health.detail) || "no detail"}); using the local hash fallback. Retrieval still works, but vectors are non-semantic until the provider is reachable.`, "warn", "alert-triangle")
        : null,
      h("dl.lt3-keyval",
        h("dt", "Provider"), h("dd", providerLabel(d.active_provider || d.provider)),
        h("dt", "Model"), h("dd", h("span.lt3-mono", d.model || d.model_id || "—")),
        h("dt", "Dimensions"), h("dd", h("span.lt3-mono", String(d.dimensions || "—"))),
        h("dt", "Status"), h("dd", embeddingStatePill(ctx, d)),
        h("dt", "Last index"), h("dd", lastIndexed),
      ),
    ),
  );
}

function providerLabel(p) {
  return ({ hash: "Local hash (fallback)", mlx: "MLX (Apple Silicon)", ollama: "Ollama",
    openai: "OpenAI-compatible", custom: "Custom" }[String(p || "hash")]) || String(p || "—");
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
function workspacePanel({ h, icon, store, c, toast, api }) {
  const ws = store.activeWorkspace();

  const orgInput = h("input.lt3-input", {
    type: "text", placeholder: "Organization name…", "aria-label": "New organization name",
    style: { "flex": "1 1 220px" },
  });
  const createBtn = h("button.lt3-btn.lt3-btn--primary", { type: "button" }, icon("plus"), "Create organization");
  const createOrg = async () => {
    const name = (orgInput.value || "").trim();
    if (!name) { toast("Enter an organization name first.", "info"); return; }
    createBtn.disabled = true;
    const res = await api.createOrg(name);
    createBtn.disabled = false;
    if (res && res.ok && res.data && !res.data.detail && !res.data.error) {
      toast(`Organization “${name}” created.`, "ok");
      orgInput.value = "";
    } else {
      const detail = (res && res.data && (res.data.detail || res.data.error)) || "the runtime is unavailable";
      toast(`Could not create organization — ${detail}.`, "warn");
    }
  };
  createBtn.addEventListener("click", createOrg);

  let savedLang = "en";
  try { savedLang = localStorage.getItem("lt3-lang") || "en"; } catch {}
  const langSelect = h("select.lt3-select", {
    "aria-label": "Interface language", value: savedLang,
    on: { change: (e) => {
      try { localStorage.setItem("lt3-lang", e.target.value); } catch {}
      toast(`Interface language set to ${e.target.selectedOptions[0].text} (saved on this device).`, "ok");
    } },
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
          createBtn,
        ),
        h("span.lt3-faint", { style: { "font-size": "var(--lt3-text-xs)" } }, "Creates a shared organization workspace on this server."),
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
        c.sourceBadge(res.source === "live" ? "live" : "unavailable"),
      ),
    );
  });
  host.replaceChildren(h("div.lt3-stack-2", rows));
}

/* ── About ──────────────────────────────────────────────────────────────── */
/* Version is read live from /health (which derives it from the backend's single
 * source of truth, WORKSPACE_OS_VERSION) — never hard-coded in the frontend.
 * If the backend is unreachable we say "unavailable" rather than inventing a
 * number. */
function aboutPanel({ h, icon, c, api }) {
  const versionSlot = h("dd", h("span.lt3-mono.lt3-faint", "checking…"));
  (async () => {
    const res = await api.raw("/health");
    const v = res && res.ok && res.data && res.data.version;
    versionSlot.replaceChildren(
      v
        ? h("span.lt3-mono", `v${String(v).replace(/^v/i, "")}`)
        : h("span.lt3-mono.lt3-faint", "unavailable"),
    );
  })();
  return c.panel({
    eyebrow: "About",
    title: "Lattice AI",
    sub: "Local-first AI workspace.",
    children: h("div.lt3-stack-4",
      h("dl.lt3-keyval",
        h("dt", "Application"), h("dd", "Lattice AI"),
        h("dt", "Version"), versionSlot,
        h("dt", "Edition"), h("dd", "Local-first AI workspace"),
      ),
    ),
  });
}

/* ── helpers ────────────────────────────────────────────────────────────── */
function titleCase(s) {
  s = String(s || "");
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
}
