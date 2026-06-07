/* ============================================================================
 * View: Pipeline — ingest / embed / graph-build flows.
 * Renders each workspace workflow as a horizontal stage flow (integration-ready
 * against /workspace/workflows and the index APIs). Pipelines execute on the
 * local runtime; this surface visualizes their stages and run state.
 * ========================================================================== */

import { timeAgo } from "../core/dom.js";

export async function render(ctx) {
  const { h, icon, api, c, toast } = ctx;

  const unavailable = (label) => () => toast(`${label} is not available from this read-only pipeline view.`, "warn");

  const statHost = h("div.lt3-statrow", c.loading({ lines: 1 }));
  const srcSlot = h("span", c.sourceBadge("pending"));
  const flowsHost = h("div.lt3-stack-6", c.loading({ lines: 3, block: true }));

  const rebuildBtn = h("button.lt3-btn.lt3-btn--primary", { on: { click: () => rebuild() } }, icon("refresh"), "Rebuild index");

  const root = h("div.lt3-stack-6",
    c.viewHeader({
      eyebrow: "Data",
      title: "Pipeline",
      sub: "Ingest, embed, and graph-build flows that turn your sources into the retrieval lattice — chunk, embed, extract entities, and link the graph.",
      actions: [rebuildBtn],
    }),
    c.banner(
      "Pipelines execute on this machine's local runtime. Use Rebuild index to re-embed every chunk and relink the knowledge graph from your current sources.",
      "info",
      "server-bolt",
    ),
    statHost,
    h("section",
      c.sectionHead("Flows", srcSlot),
      flowsHost,
    ),
  );

  load();
  return root;

  async function load() {
    const res = await api.get("/workspace/workflows", { workflows: [] });
    const pipelines = normalize(res.data);
    srcSlot.replaceChildren(c.sourceBadge(res.source));
    renderStats(pipelines);
    renderFlows(pipelines);
  }

  // Real pipeline run: rebuild the vector index (re-embed chunks, relink graph).
  async function rebuild() {
    rebuildBtn.disabled = true;
    rebuildBtn.replaceChildren(icon("loader"), "Rebuilding…");
    const res = await api.rebuildIndex();
    rebuildBtn.disabled = false;
    rebuildBtn.replaceChildren(icon("refresh"), "Rebuild index");
    if (res && res.ok && res.data && res.data.status === "completed") {
      const d = res.data;
      toast(`Index rebuilt — ${d.items_indexed} indexed, ${d.items_skipped} unchanged (${d.embedding_model}).`, "ok");
      load();
    } else {
      const detail = (res && res.data && (res.data.detail || res.data.error)) || "the knowledge graph is unavailable";
      toast(`Could not rebuild the index — ${detail}.`, "warn");
    }
  }

  function renderStats(pipelines) {
    const active = pipelines.filter((p) => isActive(p.state)).length;
    const stages = pipelines.reduce((sum, p) => sum + p.stages.length, 0);
    const throughput = pipelines.find((p) => p.throughput)?.throughput || "—";
    const lastRun = pipelines
      .map((p) => p.last_run)
      .filter(Boolean)
      .sort((a, b) => new Date(b) - new Date(a))[0];
    statHost.replaceChildren(
      c.stat({ label: "Active pipelines", value: c.fmtNum(active), icon: "player-play" }),
      c.stat({ label: "Total stages", value: c.fmtNum(stages), icon: "stack-2" }),
      c.stat({ label: "Throughput", value: throughput, icon: "gauge" }),
      c.stat({ label: "Last run", value: lastRun ? timeAgo(lastRun) : "—", icon: "history" }),
    );
  }

  function renderFlows(pipelines) {
    if (!pipelines.length) {
      flowsHost.replaceChildren(
        c.emptyState({
          icon: "git-branch-deleted",
          title: "No pipelines yet",
          body: "Connect a source and create a pipeline to ingest, embed, and build the graph.",
          action: h("button.lt3-btn.lt3-btn--ghost.lt3-btn--sm", { on: { click: () => rebuild() } }, icon("refresh"), "Rebuild index"),
        }),
      );
      return;
    }
    flowsHost.replaceChildren(...pipelines.map((p) => pipelinePanel(p)));
  }

  function pipelinePanel(p) {
    return c.panel({
      head: h("div.lt3-row", { style: { "justify-content": "space-between", "flex-wrap": "wrap", gap: "var(--lt3-space-3)" } },
        h("div.lt3-row-2",
          h("div.lt3-eyebrow", icon("git-branch"), "Pipeline"),
        ),
        c.statePill(p.state),
      ),
      children: h("div.lt3-stack-4",
        h("h3.lt3-panel__title", { style: { "margin-top": "calc(-1 * var(--lt3-space-2))" } }, p.name),
        flowDiagram(p.stages),
        pipelineFooter(p),
      ),
    });
  }

  function flowDiagram(stages) {
    const cells = [];
    stages.forEach((stage, i) => {
      if (i > 0) cells.push(h("div.lt3-flow__arrow", { "aria-hidden": "true" }, icon("chevron-right")));
      cells.push(
        h("div.lt3-stage",
          h("div.lt3-stage__num", String(i + 1).padStart(2, "0")),
          h("div.lt3-stage__name", stage),
        ),
      );
    });
    return h("div.lt3-flow", { role: "list", "aria-label": "Pipeline stages" }, cells);
  }

  function pipelineFooter(p) {
    return h("div.lt3-row", { style: { "justify-content": "space-between", "flex-wrap": "wrap", gap: "var(--lt3-space-3)" } },
      h("div.lt3-cluster",
        h("span.lt3-faint.lt3-row-2", { style: { "font-size": "var(--lt3-text-xs)" } }, icon("history"), p.last_run ? timeAgo(p.last_run) : "—"),
        h("span.lt3-faint.lt3-row-2", { style: { "font-size": "var(--lt3-text-xs)" } }, icon("gauge"), p.throughput || "—"),
      ),
      h("button.lt3-btn.lt3-btn--ghost.lt3-btn--sm", { on: { click: unavailable(`Running "${p.name}"`) } }, icon("player-play"), "Run"),
    );
  }
}

/* ── helpers ─────────────────────────────────────────────────────────────── */
function normalize(data) {
  const list = Array.isArray(data) ? data : (data && data.workflows) || [];
  return list.map((p, i) => ({
    id: p.id || `pl-${i}`,
    name: p.name || p.label || "Untitled pipeline",
    state: p.state || "idle",
    stages: Array.isArray(p.stages) ? p.stages.map((s) => String(s))
      : Array.isArray(p.steps) ? p.steps.map((s) => (s && (s.action || s.name)) || String(s))
      : [],
    last_run: p.last_run || p.created_at || null,
    throughput: p.throughput || "",
  }));
}

function isActive(state) {
  return ["active", "running", "indexing", "building"].includes(String(state).toLowerCase());
}
