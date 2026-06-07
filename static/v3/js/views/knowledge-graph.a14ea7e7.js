/* ============================================================================
 * View: Knowledge Graph — entity/relation explorer.
 * Renders the graph as an SVG mesh against /api/graph with a live inspector.
 * Missing graph data renders an empty unavailable state.
 * ========================================================================== */

import { escapeHtml } from "../core/dom.a2773eb0.js";

const TYPE_COLOR = {
  Topic: "var(--lt3-pillar-graph)",
  Concept: "var(--lt3-pillar-vector)",
  Method: "var(--lt3-pillar-hybrid)",
  Model: "var(--accent-3)",
  File: "var(--faint)",
  Decision: "var(--accent-3)",
  Task: "var(--accent-2)",
  Person: "var(--accent-pink)",
  default: "var(--accent)",
};
const colorFor = (t) => TYPE_COLOR[t] || TYPE_COLOR.default;

export async function render(ctx) {
  const { h, icon, api, store, c } = ctx;

  const state = { selected: null, query: "", data: { nodes: [], edges: [] }, source: "pending" };

  const canvasHost = h("div", c.loading({ lines: 0, block: true }));
  const inspectorHost = h("div", c.loading({ lines: 4 }));
  const statHost = h("div.lt3-statrow", c.loading({ lines: 1 }));
  const srcSlot = h("span", c.sourceBadge("pending"));

  const root = h("div.lt3-stack-6",
    c.viewHeader({
      eyebrow: "Retrieval · structure",
      title: "Knowledge Graph",
      sub: "Entities and the relations the workspace extracted between them. Click a node to trace its neighborhood.",
      actions: [
        srcSlot,
        h("button.lt3-btn.lt3-btn--ghost", { on: { click: () => load() } }, icon("refresh"), "Rebuild view"),
        h("button.lt3-btn.lt3-btn--primary", { on: { click: () => ctx.navigate("hybrid-search") } }, icon("arrows-join"), "Search graph"),
      ],
    }),
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

  function renderStats(stats, graphData) {
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

  function renderCanvas() {
    const { nodes, edges } = state.data;
    if (!nodes.length) { canvasHost.replaceChildren(c.emptyState({ icon: "chart-dots-3", title: "No entities yet", body: "Index a source to populate the graph." })); return; }
    const laidOut = layout(nodes);
    const pos = Object.fromEntries(laidOut.map((n) => [n.id, n]));
    const W = 1000, H = 600;
    const edgeSvg = edges.map((e) => {
      const a = pos[e.from], b = pos[e.to];
      if (!a || !b) return "";
      return `<line class="lt3-gedge" x1="${a.px}" y1="${a.py}" x2="${b.px}" y2="${b.py}" stroke-width="${1 + (e.weight || 1) * 0.6}"></line>`;
    }).join("");
    const nodeSvg = laidOut.map((n) => {
      const r = 10 + (n.weight || 0.5) * 16;
      const sel = state.selected === n.id;
      return `<g class="lt3-gnode" data-id="${escapeHtml(n.id)}" opacity="${state.selected && !sel && !isNeighbor(n.id) ? 0.35 : 1}">
        <circle cx="${n.px}" cy="${n.py}" r="${sel ? r + 3 : r}" fill="${colorFor(n.type)}" stroke-width="${sel ? 3 : 2}"></circle>
        <text x="${n.px}" y="${n.py + r + 13}" text-anchor="middle">${escapeHtml(truncate(n.label, 18))}</text>
      </g>`;
    }).join("");
    canvasHost.replaceChildren(
      h("div.lt3-graph-canvas", {
        html: `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Knowledge graph">${edgeSvg}${nodeSvg}</svg>`,
        on: { click: onCanvasClick },
      }),
    );
  }

  function onCanvasClick(e) {
    const g = e.target.closest(".lt3-gnode");
    if (!g) return;
    state.selected = g.dataset.id === state.selected ? null : g.dataset.id;
    renderCanvas();
    renderInspector();
  }

  function isNeighbor(id) {
    if (!state.selected) return false;
    return state.data.edges.some((e) =>
      (e.from === state.selected && e.to === id) || (e.to === state.selected && e.from === id));
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
    return h("button.lt3-entity", { on: { click: () => { state.selected = n.id; renderCanvas(); renderInspector(); } } },
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
      h("button.lt3-btn.lt3-btn--subtle.lt3-btn--sm", { on: { click: () => { state.selected = null; renderCanvas(); renderInspector(); } } }, icon("arrow-left"), "All entities"),
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
          ? h("div.lt3-stack-2", rels.map((r) => h("button.lt3-entity", { on: { click: () => { state.selected = r.other.id; renderCanvas(); renderInspector(); } } },
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

/* ── helpers ─────────────────────────────────────────────────────────────── */
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
  // Sunflower (Vogel) spread — even spacing, highest-weight entity centered.
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
  return ({ Topic: "bulb", Concept: "atom", Method: "function", Model: "cpu", File: "file", Decision: "gavel", Task: "checkbox", Person: "user" })[t] || "point";
}

function buildLegend({ h }) {
  const types = ["Topic", "Concept", "Method", "Model", "File"];
  return h("div.lt3-graph-legend",
    types.map((t) => h("span", h("i", { style: { background: colorFor(t) } }), t)),
  );
}
