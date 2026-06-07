/* ============================================================================
 * View: Workflows — workflow-driven agent execution.
 * Trigger → agent chain → tools → memory → result. Reads workflow definitions
 * and runs from the real workflow designer backend; runs a definition and shows
 * the run ledger with replay. Unavailable state is explicit.
 * ========================================================================== */

const STAGES = ["Trigger", "Agent chain", "Tools", "Memory", "Result"];

export async function render(ctx) {
  const { h, c } = ctx;
  const defsHost = h("div", c.loading({ lines: 3, block: true }));
  const runsHost = h("div", c.loading({ lines: 3 }));
  const defsSrc = h("span", c.sourceBadge("pending"));
  const runsSrc = h("span", c.sourceBadge("pending"));

  const root = h("div.lt3-stack-6",
    c.viewHeader({
      eyebrow: "Compute",
      title: "Workflow Agents",
      sub: "Repeatable automation: a trigger fires an agent chain that calls tools, reads and writes memory, and produces a result.",
    }),
    stageLegend(ctx),
    h("section", c.sectionHead("Workflow definitions", defsSrc), defsHost),
    c.panel({
      head: h("div.lt3-row", { style: { "justify-content": "space-between", width: "100%" } },
        h("div", h("div.lt3-eyebrow", "Activity"), h("h3.lt3-panel__title", "Recent runs")), runsSrc),
      children: runsHost,
    }),
  );

  loadDefs();
  loadRuns();
  return root;

  function stageLegend(ctx2) {
    return h("div.lt3-cluster", { style: { gap: "var(--lt3-space-2)" } }, STAGES.map((s, i) =>
      h("div.lt3-row-2", { style: { gap: "var(--lt3-space-2)" } }, c.pill(s, i === STAGES.length - 1 ? "warn" : "info"), i < STAGES.length - 1 ? c.icon("arrow-right") : null)));
  }

  async function loadDefs() {
    const res = await ctx.api.workflowDefinitions();
    defsSrc.replaceChildren(c.sourceBadge(res.source));
    const defs = normalizeDefs(res.data);
    if (!defs.length) {
      defsHost.replaceChildren(c.emptyState({ icon: "sitemap", title: "No workflows yet", body: res.source === "live" ? "Create a workflow definition to automate an agent chain." : "Start the backend to load workflows." }));
      return;
    }
    defsHost.replaceChildren(h("div.lt3-grid-auto", defs.map((w) => defCard(ctx, w))));
  }

  function defCard(ctx2, w) {
    const nodes = w.nodes || (w.definition && w.definition.nodes) || [];
    const triggers = nodes.filter((n) => n.type === "trigger").length || 1;
    return c.card(h("div.lt3-stack-3",
      h("div.lt3-row", { style: { "justify-content": "space-between", "align-items": "flex-start" } },
        h("div", h("b", w.name || w.id), h("div.lt3-faint", { style: { "font-size": "var(--lt3-text-2xs)", "font-family": "var(--lt3-font-mono)" } }, w.id || "")),
        c.pill(`${nodes.length || 0} nodes`),
      ),
      w.description ? h("p.lt3-muted", { style: { margin: 0, "font-size": "var(--lt3-text-sm)" } }, w.description) : null,
      h("div.lt3-cluster", (nodes.slice(0, 6)).map((n) => h("span.lt3-chip", c.icon(nodeIcon(n.type)), n.name || n.type))),
      h("div.lt3-row-2",
        h("button.lt3-btn.lt3-btn--primary.lt3-btn--sm", { on: { click: () => runDef(ctx2, w) } }, c.icon("player-play"), "Run"),
        h("span.lt3-faint", { style: { "font-size": "var(--lt3-text-2xs)" } }, `${triggers} trigger${triggers === 1 ? "" : "s"}`),
      ),
    ), { interactive: false });
  }

  async function runDef(ctx2, w) {
    const res = await ctx2.api.runWorkflow(w.id, {});
    ctx2.toast(res && res.ok ? `Ran ${w.name || w.id}` : "Run unavailable", res && res.ok ? "ok" : "err");
    loadRuns();
  }

  async function loadRuns() {
    const res = await ctx.api.workflowRuns();
    runsSrc.replaceChildren(c.sourceBadge(res.source));
    const runs = normalizeRuns(res.data);
    if (!runs.length) {
      runsHost.replaceChildren(c.emptyState({ icon: "history-off", title: "No runs yet", body: "Workflow runs will appear here once a workflow executes." }));
      return;
    }
    runsHost.replaceChildren(c.table(
      [
        { key: "status", label: "Status", width: "1%", render: (r) => c.statePill(mapStatus(r.status)) },
        { key: "name", label: "Workflow", render: (r) => h("b", { style: { "font-size": "var(--lt3-text-sm)" } }, r.workflow_name || r.workflow_id || r.id) },
        { key: "when", label: "When", width: "1%", render: (r) => h("span.lt3-faint", { style: { "white-space": "nowrap", "font-size": "var(--lt3-text-2xs)" } }, fmtTime(r.created_at || r.started_at)) },
        { key: "act", label: "", width: "1%", render: (r) => h("button.lt3-btn.lt3-btn--ghost.lt3-btn--sm", { on: { click: () => replay(ctx, r) } }, c.icon("player-track-next"), "Replay") },
      ],
      runs.slice(0, 20),
    ));
  }

  async function replay(ctx2, r) {
    const id = r.id || r.run_id;
    const res = await ctx2.api.workflowReplay(id);
    ctx2.toast(res && res.ok ? `Replay ready for ${id}` : "Replay unavailable", res && res.ok ? "ok" : "err");
  }
}

function normalizeDefs(data) {
  if (!data) return [];
  if (Array.isArray(data.workflows)) return data.workflows;
  if (Array.isArray(data.definitions)) return data.definitions;
  if (Array.isArray(data)) return data;
  return [];
}
function normalizeRuns(data) {
  if (!data) return [];
  if (Array.isArray(data.runs)) return data.runs;
  if (Array.isArray(data)) return data;
  return [];
}
function nodeIcon(type) {
  return { trigger: "bolt", agent: "robot", plugin: "puzzle", tool: "tool", output: "flag", memory: "brain" }[type] || "point";
}
function mapStatus(s) {
  const v = String(s || "").toLowerCase();
  if (v === "ok" || v === "completed" || v === "success") return "ready";
  if (v === "failed" || v === "error") return "failed";
  if (v === "running") return "active";
  return v || "idle";
}
function fmtTime(ts) {
  if (!ts) return "—";
  try { const d = new Date(ts); return Number.isNaN(d.getTime()) ? String(ts) : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }); }
  catch { return String(ts); }
}
