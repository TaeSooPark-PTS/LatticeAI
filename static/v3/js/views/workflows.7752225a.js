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
  const triggerHost = h("div", c.loading({ lines: 2 }));
  const defsSrc = h("span", c.sourceBadge("pending"));
  const runsSrc = h("span", c.sourceBadge("pending"));

  const root = h("div.lt3-stack-6",
    c.viewHeader({
      eyebrow: "Compute",
      title: "Workflow Agents",
      sub: "Repeatable automation: a trigger fires an agent chain that calls tools, reads and writes memory, and produces a result.",
    }),
    stageLegend(ctx),
    c.panel({
      eyebrow: "Triggers",
      title: "Trigger status",
      sub: "Interval and brain-event triggers armed from saved workflow definitions.",
      children: triggerHost,
    }),
    h("section", c.sectionHead("Workflow definitions", defsSrc), defsHost),
    c.panel({
      head: h("div.lt3-row", { style: { "justify-content": "space-between", width: "100%" } },
        h("div", h("div.lt3-eyebrow", "Activity"), h("h3.lt3-panel__title", "Recent runs")), runsSrc),
      children: runsHost,
    }),
  );

  loadDefs();
  loadTriggers();
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
      triggerControls(ctx2, w, nodes),
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
        { key: "act", label: "", width: "1%", render: (r) => h("div.lt3-row-2",
          isActiveStatus(r.status) ? h("button.lt3-btn.lt3-btn--danger.lt3-btn--sm", { on: { click: () => stop(ctx, r) } }, c.icon("player-stop"), "Stop") : null,
          String(r.status || "").toLowerCase() === "awaiting_approval"
            ? h("button.lt3-btn.lt3-btn--primary.lt3-btn--sm", { on: { click: () => decide(ctx, r, true) } }, c.icon("circle-check"), "Approve")
            : null,
          h("button.lt3-btn.lt3-btn--ghost.lt3-btn--sm", { on: { click: () => replay(ctx, r) } }, c.icon("player-track-next"), "Replay"),
        ) },
      ],
      runs.slice(0, 20),
    ));
  }

  async function replay(ctx2, r) {
    const id = r.id || r.run_id;
    const res = await ctx2.api.workflowReplay(id);
    ctx2.toast(res && res.ok ? `Replay ready for ${id}` : "Replay unavailable", res && res.ok ? "ok" : "err");
  }

  async function stop(ctx2, r) {
    const id = r.id || r.run_id;
    const res = await ctx2.api.stopWorkflowRun(id);
    ctx2.toast(res && res.ok ? `Stop requested for ${id}` : "Stop unavailable", res && res.ok ? "ok" : "err");
    loadRuns();
  }

  async function decide(ctx2, r, approved) {
    const id = r.id || r.run_id;
    const res = await ctx2.api.resumeWorkflowRun(id, approved);
    ctx2.toast(res && res.ok ? `Decision recorded for ${id}` : "Decision unavailable", res && res.ok ? "ok" : "err");
    loadRuns();
  }

  async function loadTriggers() {
    const res = await ctx.api.workflowTriggers();
    const armed = Array.isArray(res.data?.armed) ? res.data.armed : [];
    triggerHost.replaceChildren(
      h("div.lt3-stack-3",
        h("div.lt3-row-2", c.sourceBadge(res.source), c.statePill(res.data?.running ? "running" : "idle")),
        armed.length ? c.table([
          { key: "name", label: "Workflow", render: (r) => h("b", r.name || r.workflow_id) },
          { key: "kind", label: "Trigger", width: "1%", render: (r) => c.pill(r.kind) },
          { key: "last", label: "Last fired", width: "1%", render: (r) => fmtTime(r.last_fired_at ? Number(r.last_fired_at) * 1000 : null) },
          { key: "events", label: "Recent", render: (r) => (r.recent_events || []).slice(-2).map((e) => e.type || e.trigger).join(", ") || "—" },
        ], armed) : c.emptyState({ icon: "bolt-off", title: "No triggers armed", body: "Set a workflow trigger below to arm it." }),
      ),
    );
  }

  function triggerControls(ctx2, w, nodes) {
    const trigger = nodes.find((n) => n.type === "trigger") || {};
    const cfg = trigger.config || {};
    const kind = h("select.lt3-select", { "aria-label": "Trigger type" },
      ["manual", "interval", "brain_event"].map((value) => h("option", { value, selected: String(cfg.trigger || "manual") === value }, value)));
    const seconds = h("input.lt3-input", { type: "number", min: "60", step: "60", value: String(cfg.interval_seconds || 300), "aria-label": "Interval seconds" });
    const sourceType = h("input.lt3-input", { type: "text", value: cfg.source_type || "", placeholder: "source_type", "aria-label": "source_type" });
    async function save() {
      const updated = ensureTrigger(nodes, kind.value, Number(seconds.value) || 300, sourceType.value.trim());
      const res = await ctx2.api.updateWorkflow(w.id, { nodes: updated, metadata: { trigger_updated_at: new Date().toISOString() } });
      ctx2.toast(res && res.ok ? "Trigger saved" : "Trigger update unavailable", res && res.ok ? "ok" : "err");
      if (res && res.ok) { loadDefs(); loadTriggers(); }
    }
    return h("div.lt3-stack-2",
      h("div.lt3-eyebrow", "Trigger configuration"),
      h("div.lt3-row-2", kind, seconds, sourceType, h("button.lt3-btn.lt3-btn--subtle.lt3-btn--sm", { on: { click: save } }, c.icon("device-floppy"), "Save")),
    );
  }
}

function ensureTrigger(nodes, triggerKind, intervalSeconds, sourceType) {
  const clone = (nodes || []).map((node) => ({ ...node, config: { ...(node.config || {}) } }));
  let trigger = clone.find((n) => n.type === "trigger");
  if (!trigger) {
    trigger = { id: "trigger", type: "trigger", name: "Trigger", next: clone[0]?.id || "output", config: {} };
    clone.unshift(trigger);
    if (!clone.some((n) => n.id === "output")) clone.push({ id: "output", type: "output", name: "Output", config: {}, next: null });
  }
  trigger.config.trigger = triggerKind;
  delete trigger.config.interval_seconds;
  delete trigger.config.source_type;
  if (triggerKind === "interval") trigger.config.interval_seconds = Math.max(60, intervalSeconds || 300);
  if (triggerKind === "brain_event" && sourceType) trigger.config.source_type = sourceType;
  return clone;
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
  if (v === "running" || v === "queued" || v === "cancelling") return "active";
  if (v === "awaiting_approval") return "pending";
  if (v === "cancelled" || v === "interrupted") return "warn";
  return v || "idle";
}
function isActiveStatus(status) {
  return ["running", "queued", "in_progress", "cancelling"].includes(String(status || "").toLowerCase());
}
function fmtTime(ts) {
  if (!ts) return "—";
  try { const d = new Date(ts); return Number.isNaN(d.getTime()) ? String(ts) : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }); }
  catch { return String(ts); }
}
