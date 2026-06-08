/* ============================================================================
 * View: Home — the workspace command center.
 * Leads with the product identity (the retrieval lattice: Knowledge Graph +
 * Vector Index + Hybrid Search) and routes into every primary area.
 *
 * View contract (shared by all views):
 *   export async function render(ctx) -> single DOM node
 *   ctx = { h, icon, api, store, c, route, params, navigate, toast }
 * ========================================================================== */

export async function render(ctx) {
  const { h, icon, api, store, c, navigate } = ctx;
  const ws = store.activeWorkspace();
  const readinessHost = h("div.lt3-readiness", c.loading({ lines: 4 }));
  const activityHost = h("div", c.loading({ lines: 3 }));

  const root = h("div.lt3-stack-6",
    c.viewHeader({
      eyebrow: "Local-first AI workspace",
      title: `Welcome to ${ws.name}`,
      sub: "Everything you index stays on this machine. Ask questions, explore the graph, and fuse structure with semantics — no data leaves your computer.",
      actions: [
        h("button.lt3-btn.lt3-btn--ghost", { on: { click: () => navigate("hybrid-search") } }, icon("arrows-join"), "Hybrid search"),
        h("button.lt3-btn.lt3-btn--primary", { on: { click: () => navigate("chat", { new: "1" }) } }, icon("message-plus"), "New chat"),
      ],
    }),
    buildHero(ctx, readinessHost),
    h("section",
      c.sectionHead("Retrieval lattice", h("span", { id: "home-idx-src" }, c.sourceBadge("pending"))),
      h("div", { id: "home-pillars" }, c.loading({ lines: 2, block: true })),
    ),
    h("section",
      c.sectionHead("Jump back in"),
      buildQuickGrid(ctx),
    ),
    h("div.lt3-grid-2",
      c.panel({ eyebrow: "Index", title: "Connected sources", children: h("div", { id: "home-sources" }, c.loading({ lines: 3 })) }),
      c.panel({ eyebrow: "Workspace", title: "At a glance", children: h("div", { id: "home-stats" }, c.loading({ lines: 3 })) }),
    ),
    c.panel({
      eyebrow: "Activity",
      title: "Recent activity",
      sub: "Shown only when the local backend provides trace history.",
      children: activityHost,
      className: "lt3-panel--activity",
    }),
  );

  hydrate(ctx, root, { readinessHost, activityHost });
  return root;
}

function buildHero({ h, icon, navigate }, readinessHost) {
  return h("div.lt3-hero",
    h("div",
      h("div.lt3-eyebrow.lt3-hero__eyebrow", icon("sparkles"), "Knowledge Graph · Vector Index · Hybrid Search"),
      h("h2.lt3-hero__title", "Local workspace status, without pretending."),
      h("p.lt3-hero__sub", "Lattice shows what is ready, what is unavailable, and what needs a local runtime before Chat, Search, Knowledge, and Memory can work together."),
      h("div.lt3-hero__actions",
        h("button.lt3-btn.lt3-btn--primary.lt3-btn--lg", { on: { click: () => navigate("chat", { new: "1" }) } }, icon("message-plus"), "Start chat"),
        h("button.lt3-btn.lt3-btn--ghost.lt3-btn--lg", { on: { click: () => navigate("files") } }, icon("upload"), "Upload files"),
        h("button.lt3-btn.lt3-btn--ghost.lt3-btn--lg", { on: { click: () => navigate("models") } }, icon("cpu"), "Check models"),
      ),
      h("div.lt3-mini-lattice", { style: { "margin-top": "var(--lt3-space-6)" } },
        h("div.lt3-mini-lattice__node", h("b", "Knowledge"), h("span", "Entities and relations")),
        h("div.lt3-mini-lattice__node", h("b", "Vectors"), h("span", "Local semantic recall")),
        h("div.lt3-mini-lattice__node", h("b", "Hybrid"), h("span", "Fused answer grounding")),
      ),
    ),
    h("aside.lt3-hero__aside",
      h("div.lt3-eyebrow", "Readiness"),
      readinessHost,
    ),
  );
}

const QUICK = [
  { key: "chat", icon: "message-2", title: "Chat", desc: "Grounded conversation." },
  { key: "files", icon: "folders", title: "Files", desc: "Sources and indexing." },
  { key: "hybrid-search", icon: "arrows-join", title: "Search", desc: "Fuse graph + vector recall." },
  { key: "knowledge-graph", icon: "chart-dots-3", title: "Knowledge", desc: "Browse entities and relations." },
  { key: "memory", icon: "brain", title: "Memory", desc: "Inspect long-term recall." },
  { key: "models", icon: "cpu", title: "Models", desc: "Local MLX runtime." },
];

function buildQuickGrid({ h, icon, navigate }) {
  return h("div.lt3-quickgrid",
    QUICK.map((q) => h("button.lt3-quick", { style: { "text-align": "left" }, on: { click: () => navigate(q.key) } },
      h("div.lt3-quick__icon", icon(q.icon)),
      h("div.lt3-quick__title", q.title),
      h("div.lt3-quick__desc", q.desc),
    )),
  );
}

async function hydrate(ctx, root, hosts) {
  const { h, icon, api, store, c } = ctx;
  const { readinessHost, activityHost } = hosts;
  const numFmt = c.fmtNum;

  // Index status → pillars + sources + topbar chip.
  const idx = store.get().indexStatus
    ? { data: store.get().indexStatus, source: "live" }
    : await api.indexStatus().then((r) => { store.setIndexStatus(r.data); return r; });

  root.querySelector("#home-idx-src")?.replaceChildren(c.sourceBadge(idx.source));
  root.querySelector("#home-pillars")?.replaceChildren(c.pillars(idx.data));

  const sources = (idx.data && idx.data.sources) || [];
  const srcHost = root.querySelector("#home-sources");
  if (srcHost) {
    srcHost.replaceChildren(
      sources.length
        ? h("div.lt3-stack-3", sources.map((s) => h("div.lt3-stack-2",
            h("div.lt3-row", { style: { "justify-content": "space-between" } },
              h("div.lt3-row-2", icon("database"), h("b", { style: { "font-size": "var(--lt3-text-sm)" } }, s.label)),
              c.statePill(s.state),
            ),
            c.meter(s.progress ?? (s.state === "indexed" ? 1 : 0.5), s.state === "indexing" ? "warn" : "vector"),
            h("div.lt3-faint", { style: { "font-size": "var(--lt3-text-2xs)" } }, `${numFmt(s.files)} files`),
          )))
        : c.emptyState({ icon: "database-off", title: "No sources connected", body: "Upload documents to start indexing. Folder watching requires the desktop local agent." }),
    );
  }

  const [os, models, memory, traces] = await Promise.all([
    api.workspaceOs(),
    api.models(),
    api.memoryManager(),
    api.get("/workspace/traces", { traces: [] }),
  ]);
  renderReadiness({ h, icon, c, readinessHost, idx, models, os, memory });

  // Workspace counts.
  const counts = (os.data && os.data.counts) || {};
  const statHost = root.querySelector("#home-stats");
  if (statHost) {
    statHost.replaceChildren(
      h("div.lt3-statrow",
        c.stat({ label: "Memories", value: numFmt(counts.memories), icon: "brain" }),
        c.stat({ label: "Traces", value: numFmt(counts.traces), icon: "route" }),
        c.stat({ label: "Workflows", value: numFmt(counts.workflows), icon: "git-branch" }),
        c.stat({ label: "Skills", value: numFmt(counts.skills), icon: "puzzle" }),
      ),
      h("div", { style: { "margin-top": "var(--lt3-space-3)" } }, c.sourceBadge(os.source)),
    );
  }

  renderActivity({ h, icon, c, activityHost, traces });
}

function renderReadiness({ h, icon, c, readinessHost, idx, models, os, memory }) {
  const pipes = (idx.data && idx.data.pipelines) || {};
  const vectorReady = String(pipes.vector_index?.state || "").toLowerCase() === "ready";
  const graphReady = String(pipes.knowledge_graph?.state || "").toLowerCase() === "ready";
  const modelName = models.data && models.data.current;
  const counts = (os.data && os.data.counts) || {};
  const memSources = (memory.data && memory.data.sources) || [];
  readinessHost.replaceChildren(
    readinessRow({ h, icon, c, ic: "server", title: "Backend", meta: idx.source === "live" ? "Live local API" : "Local API unavailable", state: idx.source === "live" ? "ready" : "pending" }),
    readinessRow({ h, icon, c, ic: "cpu", title: "Model", meta: modelName ? shortModel(modelName) : "No model loaded", state: modelName ? "ready" : "pending" }),
    readinessRow({ h, icon, c, ic: "database", title: "Retrieval", meta: graphReady && vectorReady ? "Graph and vector ready" : "Index needs data or rebuild", state: graphReady && vectorReady ? "ready" : "pending" }),
    readinessRow({ h, icon, c, ic: "brain", title: "Memory", meta: memSources.length ? `${c.fmtNum(counts.memories)} memories across ${memSources.length} tiers` : "Memory backend unavailable or empty", state: memSources.length ? "ready" : "idle" }),
  );
}

function readinessRow({ h, icon, c, ic, title, meta, state }) {
  return h("div.lt3-readiness__row",
    h("div.lt3-readiness__icon", icon(ic)),
    h("div", h("div.lt3-readiness__title", title), h("div.lt3-readiness__meta", meta)),
    c.statePill(state),
  );
}

function renderActivity({ h, icon, c, activityHost, traces }) {
  const rows = (traces.data && Array.isArray(traces.data.traces)) ? traces.data.traces : [];
  if (!rows.length) {
    activityHost.replaceChildren(c.emptyState({
      icon: "history-off",
      title: "No recent activity available",
      body: traces.source === "live" ? "The backend returned no trace history yet." : "Start the backend to show recent local workspace activity.",
    }));
    return;
  }
  activityHost.replaceChildren(h("div.lt3-list", rows.slice(0, 6).map((tr) =>
    h("div.lt3-list__item",
      h("span.lt3-avatar", { style: { width: "28px", height: "28px" } }, icon("route")),
      h("div.lt3-list__body",
        h("div.lt3-list__title", tr.question || tr.event_type || "Workspace event"),
        h("div.lt3-list__meta", [tr.confidence != null ? `${Math.round(Number(tr.confidence) * 100)}% confidence` : null, tr.created_at || tr.timestamp || null].filter(Boolean).join(" · ")),
      ),
      c.sourceBadge(traces.source),
    ))));
}

function shortModel(id) {
  const s = String(id || "");
  const tail = s.includes("/") ? s.split("/").pop() : s;
  return tail.length > 28 ? tail.slice(0, 27) + "…" : tail;
}
