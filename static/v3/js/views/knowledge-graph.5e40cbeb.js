/* ============================================================================
 * View: Knowledge Graph — the user's digital brain (v3.6.0 Knowledge Graph First).
 * Tabs: Explore (entity/relation mesh) · Status (graph + ingestion health) ·
 * Sources (where every node came from) · Capture (web/URL into the graph) ·
 * Backup (local export / import / backup). Everything you ingest converges here;
 * models read this graph; local-first keeps it yours. Missing data renders an
 * honest unavailable state — never fabricated counters.
 * ========================================================================== */

import { escapeHtml } from "../core/dom.a2773eb0.js";
import { createGraphCanvas } from "./graph-canvas.17c15d65.js";

const TYPE_COLOR = {
  Topic: "var(--lt3-pillar-graph)",
  Concept: "var(--lt3-pillar-vector)",
  Method: "var(--lt3-pillar-hybrid)",
  Model: "var(--accent-3)",
  File: "var(--faint)",
  Source: "var(--lt3-pillar-hybrid)",
  Document: "var(--accent)",
  Decision: "var(--accent-3)",
  Task: "var(--accent-2)",
  Person: "var(--accent-pink)",
  default: "var(--accent)",
};
const colorFor = (t) => TYPE_COLOR[t] || TYPE_COLOR.default;

const TABS = [
  { key: "explore", label: "Explore", icon: "chart-dots-3" },
  { key: "status", label: "Status", icon: "activity-heartbeat" },
  { key: "sources", label: "Sources", icon: "database" },
  { key: "capture", label: "Capture", icon: "world-www" },
  { key: "portability", label: "Backup", icon: "archive" },
];

export async function render(ctx) {
  const { h, icon, api, c } = ctx;
  let active = "explore";

  const tabBar = h("div.lt3-row-2");
  const panelHost = h("div.lt3-stack-4");

  function renderTabs() {
    tabBar.replaceChildren(...TABS.map((t) =>
      h("button.lt3-btn" + (t.key === active ? ".lt3-btn--primary" : ".lt3-btn--ghost"),
        { on: { click: () => switchTab(t.key) } }, icon(t.icon), t.label)));
  }

  function switchTab(key) {
    if (active === key) return;
    active = key;
    renderTabs();
    renderActive();
  }

  let exploreNode = null;
  function renderActive() {
    if (active === "explore") {
      if (!exploreNode) exploreNode = buildExplore(ctx);
      panelHost.replaceChildren(exploreNode);
    } else if (active === "status") {
      renderStatus(ctx, panelHost);
    } else if (active === "sources") {
      renderSources(ctx, panelHost);
    } else if (active === "capture") {
      panelHost.replaceChildren(buildCapture(ctx));
    } else if (active === "portability") {
      renderPortability(ctx, panelHost);
    }
  }

  const root = h("div.lt3-stack-6",
    c.viewHeader({
      eyebrow: "Your digital brain",
      title: "Knowledge Graph",
      sub: "Everything you ingest converges here — files, folders, web pages, browser tabs. Models read this graph; local-first keeps it yours.",
      actions: [
        h("button.lt3-btn.lt3-btn--ghost", { on: { click: () => ctx.navigate("files") } }, icon("upload"), "Add sources"),
        h("button.lt3-btn.lt3-btn--primary", { on: { click: () => ctx.navigate("hybrid-search") } }, icon("arrows-join"), "Search graph"),
      ],
    }),
    tabBar,
    panelHost,
  );

  renderTabs();
  renderActive();
  return root;
}

/* ── Explore tab (entity/relation mesh) ─────────────────────────────────── */
function buildExplore(ctx) {
  const { h, icon, api, c } = ctx;
  const state = { selected: null, query: "", data: { nodes: [], edges: [] }, source: "pending" };

  const canvasHost = h("div", c.loading({ lines: 0, block: true }));
  const inspectorHost = h("div", c.loading({ lines: 4 }));
  const statHost = h("div.lt3-statrow", c.loading({ lines: 1 }));
  const srcSlot = h("span", c.sourceBadge("pending"));

  const root = h("div.lt3-stack-3",
    h("div.lt3-row-2",
      srcSlot,
      h("button.lt3-btn.lt3-btn--ghost.lt3-btn--sm", { on: { click: () => load() } }, icon("refresh"), "Rebuild view"),
    ),
    statHost,
    h("div.lt3-split",
      h("div.lt3-stack-3",
        c.card(canvasHost, { attrs: { style: "padding:0;overflow:hidden" } }),
        buildLegend(ctx),
      ),
      h("aside.lt3-panel",
        h("div.lt3-panel__head", h("div", h("div.lt3-eyebrow", "Inspector"), h("h3.lt3-panel__title", "Entities"))),
        h("div.lt3-search", { style: { "margin-bottom": "var(--lt3-space-4)" } },
          icon("search"),
          h("input", { type: "text", placeholder: "Filter entities…", "aria-label": "Filter entities",
            on: { input: (e) => { state.query = e.target.value.toLowerCase(); renderInspector(); } } }),
        ),
        inspectorHost,
      ),
    ),
  );

  async function load() {
    canvasHost.replaceChildren(c.loading({ lines: 0, block: true }));
    const [g, stats] = await Promise.all([api.graph(), api.graphStats()]);
    state.data = normalize(g.data);
    state.source = g.source;
    srcSlot.replaceChildren(c.sourceBadge(g.source));
    renderStats(stats.data, g.data);
    renderCanvas();
    renderInspector();
  }

  function renderStats(stats) {
    const nodes = state.data.nodes.length;
    const edges = state.data.edges.length;
    const types = stats && stats.nodes ? Object.keys(stats.nodes).length : new Set(state.data.nodes.map((n) => n.type)).size;
    const density = nodes > 1 ? (edges / (nodes * (nodes - 1) / 2)) : 0;
    statHost.replaceChildren(
      c.stat({ label: "Entities", value: c.fmtNum(nodes), icon: "circles" }),
      c.stat({ label: "Relations", value: c.fmtNum(edges), icon: "vector-triangle" }),
      c.stat({ label: "Entity types", value: types, icon: "category" }),
      c.stat({ label: "Density", value: density.toFixed(2), icon: "chart-dots" }),
    );
  }

  // Live force-directed canvas (zoom / pan / drag / physics) — replaces the
  // static SVG spiral. The renderer only draws the data it is given.
  let graphCanvas = null;

  function ensureGraphCanvas() {
    if (graphCanvas) return graphCanvas;
    graphCanvas = createGraphCanvas({
      colorFor,
      onSelect: (id) => {
        state.selected = id;
        renderInspector();
      },
    });
    return graphCanvas;
  }

  function renderCanvas() {
    const { nodes, edges } = state.data;
    if (!nodes.length) {
      if (graphCanvas) { graphCanvas.destroy(); graphCanvas = null; }
      canvasHost.replaceChildren(c.emptyState({ icon: "chart-dots-3", title: "No entities yet", body: "Index a source to populate the graph." }));
      return;
    }
    const gc = ensureGraphCanvas();
    gc.setData({ nodes, edges });
    gc.setSelected(state.selected);
    if (!gc.el.isConnected || gc.el.parentElement !== canvasHost.querySelector(".lt3-graph-canvas")) {
      canvasHost.replaceChildren(h("div.lt3-graph-canvas", gc.el));
    }
  }

  function syncSelection() {
    if (graphCanvas) graphCanvas.setSelected(state.selected);
    renderInspector();
  }

  function renderInspector() {
    if (state.selected) { inspectorHost.replaceChildren(detailView()); return; }
    const q = state.query;
    const list = state.data.nodes
      .filter((n) => !q || (n.label || "").toLowerCase().includes(q) || (n.type || "").toLowerCase().includes(q))
      .sort((a, b) => (b.weight || 0) - (a.weight || 0));
    inspectorHost.replaceChildren(
      list.length
        ? h("div.lt3-stack-2", list.slice(0, 60).map((n) => entityRow(n)))
        : c.emptyState({ icon: "search-off", title: "No matches", body: "Try a different entity name." }),
    );
  }

  function entityRow(n) {
    return h("button.lt3-entity", { on: { click: () => { state.selected = n.id; syncSelection(); } } },
      h("div.lt3-entity__type", { style: { background: `color-mix(in srgb, ${colorFor(n.type)} 18%, transparent)`, color: colorFor(n.type) } }, icon(iconForType(n.type))),
      h("div.lt3-entity__body",
        h("div.lt3-entity__name", n.label),
        h("div.lt3-entity__meta", `${n.type || "Entity"} · weight ${(n.weight || 0).toFixed(2)}`),
      ),
    );
  }

  function detailView() {
    const n = state.data.nodes.find((x) => x.id === state.selected);
    if (!n) { state.selected = null; return c.emptyState({ title: "Not found" }); }
    const rels = state.data.edges
      .filter((e) => e.from === n.id || e.to === n.id)
      .map((e) => {
        const otherId = e.from === n.id ? e.to : e.from;
        const other = state.data.nodes.find((x) => x.id === otherId);
        return { type: e.type, dir: e.from === n.id ? "→" : "←", other };
      })
      .filter((r) => r.other);
    return h("div.lt3-stack-4",
      h("button.lt3-btn.lt3-btn--subtle.lt3-btn--sm", { on: { click: () => { state.selected = null; syncSelection(); } } }, icon("arrow-left"), "All entities"),
      h("div.lt3-card.lt3-card--flat",
        h("div.lt3-row-2", { style: { "margin-bottom": "var(--lt3-space-2)" } },
          h("span.lt3-pill", { style: { color: colorFor(n.type) } }, n.type || "Entity"),
        ),
        h("div", { style: { "font-size": "var(--lt3-text-lg)", "font-weight": 700 } }, n.label),
        n.summary && h("p.lt3-muted", { style: { "font-size": "var(--lt3-text-sm)", "margin-top": "var(--lt3-space-2)" } }, n.summary),
      ),
      h("div",
        h("div.lt3-eyebrow", { style: { "margin-bottom": "var(--lt3-space-2)" } }, `Relations (${rels.length})`),
        rels.length
          ? h("div.lt3-stack-2", rels.map((r) => h("button.lt3-entity", { on: { click: () => { state.selected = r.other.id; syncSelection(); } } },
              h("div.lt3-entity__type", { style: { background: "var(--surface-3)" } }, h("span.lt3-mono", { style: { "font-size": "var(--lt3-text-sm)" } }, r.dir)),
              h("div.lt3-entity__body",
                h("div.lt3-entity__name", r.other.label),
                h("div.lt3-entity__meta", r.type),
              ),
            )))
          : c.emptyState({ icon: "unlink", title: "No relations", body: "This entity is currently isolated." }),
      ),
    );
  }

  load();
  return root;
}

/* ── Status tab (graph + ingestion health) ──────────────────────────────── */
async function renderStatus(ctx, host) {
  const { h, icon, api, c } = ctx;
  host.replaceChildren(c.loading({ lines: 3 }));
  const [port, gs, idx] = await Promise.all([api.kgPortability(), api.graphStats(), api.indexStatus()]);
  const p = port.data || {};
  const prov = p.provenance || {};
  const nodes = sumCounts((gs.data && gs.data.nodes) || {});
  const edges = sumCounts((gs.data && gs.data.edges) || {});
  const pipelines = (idx.data && idx.data.pipelines) || {};

  host.replaceChildren(
    h("div.lt3-row-2", c.sourceBadge(port.source), h("span.lt3-muted", { style: { "font-size": "var(--lt3-text-sm)" } },
      p.graph_schema_version != null ? `Schema v${p.graph_schema_version} · embed dim ${p.embed_dim ?? "—"}` : "Knowledge Graph status")),
    h("div.lt3-statrow",
      c.stat({ label: "Entities", value: c.fmtNum(nodes), icon: "circles" }),
      c.stat({ label: "Relations", value: c.fmtNum(edges), icon: "vector-triangle" }),
      c.stat({ label: "Ingested items", value: c.fmtNum(prov.total || 0), icon: "package-import" }),
      c.stat({ label: "Embedded (RAG-ready)", value: c.fmtNum(prov.embedded || 0), icon: "vector" }),
    ),
    c.card(
      h("div.lt3-stack-3",
        h("div.lt3-eyebrow", "Pipelines"),
        pipelineRow(ctx, "Knowledge graph", pipelines.knowledge_graph),
        pipelineRow(ctx, "Vector index", pipelines.vector_index),
        pipelineRow(ctx, "Hybrid retrieval", pipelines.hybrid),
      ),
    ),
    prov.last_ingested_at
      ? h("p.lt3-muted", { style: { "font-size": "var(--lt3-text-sm)" } }, `Last ingestion: ${fmtWhen(prov.last_ingested_at)} · ${prov.duplicates || 0} duplicate(s) linked, not re-stored.`)
      : c.emptyState({ icon: "package-import", title: "Nothing ingested yet", body: "Add files or capture a page to populate the graph." }),
  );
}

function pipelineRow(ctx, label, pipe) {
  const { h, c } = ctx;
  const stateStr = (pipe && pipe.state) || "unavailable";
  const detail = pipe && (pipe.entities != null ? `${pipe.entities} entities` : pipe.vectors != null ? `${pipe.vectors} vectors` : pipe.strategy || "");
  return h("div.lt3-row-2", { style: { "justify-content": "space-between" } },
    h("div", label, detail ? h("span.lt3-muted", { style: { "margin-left": "var(--lt3-space-2)", "font-size": "var(--lt3-text-sm)" } }, detail) : null),
    c.statePill(stateStr),
  );
}

/* ── Sources tab (provenance: where every node came from) ────────────────── */
async function renderSources(ctx, host) {
  const { h, icon, api, c } = ctx;
  host.replaceChildren(c.loading({ lines: 3 }));
  const [port, recent] = await Promise.all([api.kgPortability(), api.kgProvenance(40)]);
  const bySource = (port.data && port.data.provenance && port.data.provenance.by_source_type) || {};
  const items = (recent.data && recent.data.items) || [];

  const sourceCards = Object.keys(bySource).length
    ? h("div.lt3-statrow", Object.entries(bySource).map(([k, v]) =>
        c.stat({ label: prettySource(k), value: c.fmtNum(v), icon: iconForSource(k) })))
    : c.emptyState({ icon: "database", title: "No sources yet", body: "Connect a folder, upload a file, or capture a page." });

  const recentList = items.length
    ? h("div.lt3-stack-2", items.map((it) =>
        h("div.lt3-entity",
          h("div.lt3-entity__type", { style: { background: "var(--surface-3)", color: colorFor("Source") } }, icon(iconForSource(it.source_type))),
          h("div.lt3-entity__body",
            h("div.lt3-entity__name", it.title || it.source_uri || it.node_id),
            h("div.lt3-entity__meta", `${prettySource(it.source_type)} · ${fmtWhen(it.created_at)}${it.embedded ? " · embedded" : ""}${it.duplicate ? " · duplicate" : ""}`),
          ),
        )))
    : c.emptyState({ icon: "history", title: "No recent ingestions", body: "Ingested items will appear here with full provenance." });

  host.replaceChildren(
    h("div.lt3-row-2", c.sourceBadge(port.source), h("span.lt3-muted", { style: { "font-size": "var(--lt3-text-sm)" } }, "Every node records where it came from (provenance).")),
    sourceCards,
    c.card(h("div.lt3-stack-3", h("div.lt3-eyebrow", "Recent ingestions"), recentList)),
  );
}

/* ── Capture tab (web/URL into the graph) ───────────────────────────────── */
function buildCapture(ctx) {
  const { h, icon, api, c } = ctx;
  const input = h("input", { type: "url", placeholder: "https://example.com/article", "aria-label": "URL to capture",
    style: { flex: "1" } });
  const result = h("div");

  async function run() {
    const url = (input.value || "").trim();
    if (!url) { result.replaceChildren(c.banner({ tone: "warn", text: "Enter a URL first." })); return; }
    result.replaceChildren(c.loading({ lines: 1 }));
    const res = await api.browserReadUrl(url);
    const d = res.data || {};
    if (res.ok && d.status === "ok") {
      result.replaceChildren(c.banner({ tone: "ok", text: `Added to your Knowledge Graph${d.duplicate ? " (already present — linked)" : ""}. ${d.chunk_count || 0} chunk(s) indexed.` }));
      ctx.toast && ctx.toast("Page added to Knowledge Graph");
    } else if (d.status === "empty") {
      result.replaceChildren(c.banner({ tone: "warn", text: "No readable text was found on that page." }));
    } else {
      const detail = d.detail || (res.status === 422 ? "The page is blocked or login-required." : "Could not read that URL.");
      result.replaceChildren(c.banner({ tone: "err", text: detail }));
    }
  }

  return h("div.lt3-stack-4",
    c.card(h("div.lt3-stack-3",
      h("div.lt3-eyebrow", "Capture a web page"),
      h("p.lt3-muted", { style: { "font-size": "var(--lt3-text-sm)" } }, "The local runtime fetches the page, extracts readable text, and indexes it into your graph as a web source. Nothing is sent to a cloud service."),
      h("div.lt3-row-2",
        input,
        h("button.lt3-btn.lt3-btn--primary", { on: { click: run } }, icon("world-download"), "Read into graph"),
      ),
      result,
    )),
    c.card(h("div.lt3-stack-2",
      h("div.lt3-eyebrow", "Browser extension"),
      h("p.lt3-muted", { style: { "font-size": "var(--lt3-text-sm)" } }, "Install the local Manifest V3 extension (browser-extension/) to send the current tab to your Knowledge Graph with one click. It posts only to 127.0.0.1 — never to a cloud server."),
    )),
  );
}

/* ── Portability tab (export / import / backup) ─────────────────────────── */
async function renderPortability(ctx, host) {
  const { h, icon, api, c } = ctx;
  host.replaceChildren(c.loading({ lines: 2 }));
  const port = await api.kgPortability();
  const status = h("div");

  function note(tone, text) { status.replaceChildren(c.banner({ tone, text })); }

  async function doExport() {
    note("info", "Exporting…");
    const res = await api.graphExport();
    if (!res.ok || !res.data || res.data.raw) { note("err", "Export is unavailable."); return; }
    try {
      const blob = new Blob([JSON.stringify(res.data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = "lattice-kg-export.json"; a.click();
      URL.revokeObjectURL(url);
      note("ok", `Exported ${(res.data.counts && res.data.counts.nodes) || 0} nodes. Download started.`);
    } catch (e) { note("err", "Could not build the download."); }
  }

  async function doBackup() {
    note("info", "Backing up…");
    const res = await api.graphBackup();
    if (res.ok && res.data && res.data.path) {
      note("ok", `Backup written locally: ${res.data.path}`);
    } else {
      note("err", (res.data && (res.data.detail || res.data.error)) || "Backup requires admin and a running runtime.");
    }
  }

  const importArea = h("textarea", { rows: 5, placeholder: "Paste a Knowledge Graph export (JSON) to validate, then import…",
    "aria-label": "Import artifact", style: { width: "100%", "font-family": "var(--lt3-font-mono)", "font-size": "var(--lt3-text-sm)" } });

  async function doImport(dryRun) {
    let artifact;
    try { artifact = JSON.parse(importArea.value || ""); }
    catch { note("err", "That is not valid JSON."); return; }
    note("info", dryRun ? "Validating…" : "Importing…");
    const res = await api.graphImport(artifact, "merge", dryRun);
    if (res.ok && res.data && !res.data.detail) {
      const d = res.data;
      note("ok", dryRun
        ? `Valid — would import ${d.nodes || 0} nodes, ${d.edges || 0} edges.`
        : `Imported ${d.nodes || 0} nodes, ${d.edges || 0} edges.`);
    } else {
      note("err", (res.data && (res.data.detail || res.data.error)) || "Import requires admin.");
    }
  }

  const p = port.data || {};
  host.replaceChildren(
    h("div.lt3-row-2", c.sourceBadge(port.source), h("span.lt3-muted", { style: { "font-size": "var(--lt3-text-sm)" } }, "The Knowledge Graph is your durable asset — portable with no cloud.")),
    c.card(h("div.lt3-stack-3",
      h("div.lt3-eyebrow", "Export & backup"),
      h("p.lt3-muted", { style: { "font-size": "var(--lt3-text-sm)" } }, "Export a portable JSON of nodes/edges/provenance, or write a full local binary backup (DB + blobs)."),
      h("div.lt3-row-2",
        h("button.lt3-btn.lt3-btn--primary", { on: { click: doExport } }, icon("download"), "Export JSON"),
        h("button.lt3-btn.lt3-btn--ghost", { on: { click: doBackup } }, icon("archive"), "Backup (admin)"),
      ),
    )),
    c.card(h("div.lt3-stack-3",
      h("div.lt3-eyebrow", "Import"),
      importArea,
      h("div.lt3-row-2",
        h("button.lt3-btn.lt3-btn--ghost", { on: { click: () => doImport(true) } }, icon("checks"), "Validate (dry-run)"),
        h("button.lt3-btn.lt3-btn--primary", { on: { click: () => doImport(false) } }, icon("file-import"), "Import (merge, admin)"),
      ),
    )),
    status,
  );
}

/* ── helpers ─────────────────────────────────────────────────────────────── */
function sumCounts(obj) {
  return Object.values(obj || {}).reduce((a, b) => a + (Number(b) || 0), 0);
}

function prettySource(k) {
  return ({ web_url: "Web URL", browser_tab: "Browser tab", file: "Files", local_file: "Local files",
    note: "Notes", text: "Text", markdown: "Markdown", code: "Code", upload: "Uploads" })[k] || k;
}

function iconForSource(k) {
  return ({ web_url: "world", browser_tab: "browser", file: "file", local_file: "folder",
    note: "note", text: "text-caption", markdown: "markdown", code: "code", upload: "upload" })[k] || "database";
}

function fmtWhen(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso);
    return d.toLocaleString();
  } catch { return String(iso); }
}

function normalize(data) {
  const nodes = (data.nodes || []).map((n) => ({
    id: n.id,
    label: n.label || n.title || n.id,
    type: n.type || "Entity",
    weight: n.weight ?? n.importance_norm ?? (n.metadata && n.metadata.graph_metrics && n.metadata.graph_metrics.importance_norm) ?? 0.5,
    summary: n.summary || "",
    x: n.x, y: n.y,
  }));
  const ids = new Set(nodes.map((n) => n.id));
  const edges = (data.edges || []).filter((e) => ids.has(e.from) && ids.has(e.to))
    .map((e) => ({ from: e.from, to: e.to, type: e.type || "related", weight: e.weight || 1 }));
  return { nodes, edges };
}

function layout(nodes) {
  const W = 1000, H = 600, cx = W / 2, cy = H / 2;
  const golden = Math.PI * (3 - Math.sqrt(5));
  const hasCoords = nodes.length && nodes.every((n) => typeof n.x === "number" && typeof n.y === "number");
  if (hasCoords) {
    return nodes.map((n) => ({ ...n, px: Math.round(60 + n.x * (W - 120)), py: Math.round(50 + n.y * (H - 100)) }));
  }
  const order = nodes.map((n, i) => ({ n, i })).sort((a, b) => (b.n.weight || 0) - (a.n.weight || 0));
  const maxR = Math.min(W, H) * 0.42;
  const placed = {};
  order.forEach((o, rank) => {
    const radius = rank === 0 ? 0 : maxR * Math.sqrt(rank / Math.max(1, nodes.length - 1));
    const angle = rank * golden;
    placed[o.i] = {
      px: Math.round(cx + Math.cos(angle) * radius),
      py: Math.round(cy + Math.sin(angle) * radius * 0.66),
    };
  });
  return nodes.map((n, i) => ({ ...n, ...placed[i] }));
}

function truncate(s, n) { s = String(s || ""); return s.length > n ? s.slice(0, n - 1) + "…" : s; }

function iconForType(t) {
  return ({ Topic: "bulb", Concept: "atom", Method: "function", Model: "cpu", File: "file", Source: "world",
    Document: "file-text", Decision: "gavel", Task: "checkbox", Person: "user" })[t] || "point";
}

function buildLegend({ h }) {
  const types = ["Source", "Document", "Concept", "Person", "Decision"];
  return h("div.lt3-graph-legend",
    types.map((t) => h("span", h("i", { style: { background: colorFor(t) } }), t)),
  );
}
