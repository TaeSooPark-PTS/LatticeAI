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
    buildHero(ctx),
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
  );

  hydrate(ctx, root);
  return root;
}

function buildHero({ h, icon, navigate }) {
  return h("div.lt3-hero",
    h("div.lt3-eyebrow.lt3-hero__eyebrow", icon("sparkles"), "Knowledge Graph · Vector Index · Hybrid Search"),
    h("h2.lt3-hero__title", "One workspace. Three ways to recall everything."),
    h("p.lt3-hero__sub", "Lattice builds a knowledge graph and a vector field from your files, then fuses them so every answer is grounded in both structure and meaning."),
    h("div.lt3-hero__actions",
      h("button.lt3-btn.lt3-btn--primary.lt3-btn--lg", { on: { click: () => navigate("knowledge-graph") } }, icon("chart-dots-3"), "Explore the graph"),
      h("button.lt3-btn.lt3-btn--ghost.lt3-btn--lg", { on: { click: () => navigate("files") } }, icon("folder-plus"), "Connect files"),
    ),
  );
}

const QUICK = [
  { key: "knowledge-graph", icon: "chart-dots-3", title: "Knowledge Graph", desc: "Browse entities and relations." },
  { key: "hybrid-search", icon: "arrows-join", title: "Hybrid Search", desc: "Fuse graph + vector recall." },
  { key: "chat", icon: "message-2", title: "Chat", desc: "Grounded conversation." },
  { key: "files", icon: "folders", title: "Files", desc: "Sources and indexing." },
  { key: "pipeline", icon: "git-branch", title: "Pipeline", desc: "Ingest and embed flows." },
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

async function hydrate(ctx, root) {
  const { h, icon, api, store, c } = ctx;
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
        : c.emptyState({ icon: "database-off", title: "No sources connected", body: "Connect a folder to start indexing." }),
    );
  }

  // Workspace counts.
  const os = await api.workspaceOs();
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
}
