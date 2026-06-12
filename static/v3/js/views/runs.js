import { t } from "../core/i18n.js";

const ACTIVE = new Set(["queued", "running", "in_progress", "active", "cancelling"]);

export async function render(ctx) {
  const { h, icon, api, c, toast } = ctx;
  const agentHost = h("div", c.loading({ lines: 4 }));
  const workflowHost = h("div", c.loading({ lines: 4 }));
  const approvalHost = h("div", c.loading({ lines: 4 }));
  const progressHost = h("div", c.loading({ lines: 3 }));

  const root = h("div.lt3-stack-6",
    c.viewHeader({ eyebrow: t("runs.eyebrow"), title: t("runs.title"), sub: t("runs.sub") }),
    h("div.lt3-statrow", progressHost),
    c.panel({ title: t("runs.approvals"), children: approvalHost }),
    c.panel({ title: t("runs.agentRuns"), children: agentHost }),
    c.panel({ title: t("runs.workflowRuns"), children: workflowHost }),
  );

  await load();
  const poll = setInterval(load, 5000);
  root.addEventListener("DOMNodeRemovedFromDocument", () => clearInterval(poll), { once: true });
  return root;

  async function load() {
    const [agent, workflow, pending] = await Promise.all([
      api.agentRuntime(),
      api.workflowRuns(),
      api.permissionsPending(),
    ]);
    const agentRuns = Array.isArray(agent.data?.runs) ? agent.data.runs : [];
    const workflowRuns = Array.isArray(workflow.data?.runs) ? workflow.data.runs : [];
    renderProgress(agentRuns, workflowRuns, pending.data || {});
    agentHost.replaceChildren(runTable(ctx, agentRuns, "agent", agent.source));
    workflowHost.replaceChildren(runTable(ctx, workflowRuns, "workflow", workflow.source));
    approvalHost.replaceChildren(approvalList(ctx, workflowRuns, pending.data || {}, workflow.source || pending.source));
  }

  function renderProgress(agentRuns, workflowRuns, pending) {
    const all = [...agentRuns, ...workflowRuns];
    const active = all.filter((r) => ACTIVE.has(String(r.status || "").toLowerCase())).length;
    const paused = workflowRuns.filter((r) => String(r.status || "").toLowerCase() === "awaiting_approval").length;
    const approvals = Object.keys(pending.pending || {}).length + paused;
    progressHost.replaceChildren(
      c.stat({ label: t("runs.progress"), value: String(active), icon: "progress" }),
      c.stat({ label: t("runs.approvals"), value: String(approvals), icon: "circle-check" }),
      c.stat({ label: t("runs.agentRuns"), value: String(agentRuns.length), icon: "robot" }),
      c.stat({ label: t("runs.workflowRuns"), value: String(workflowRuns.length), icon: "sitemap" }),
    );
  }

  function runTable(ctx2, rows, kind, source) {
    const { h, c } = ctx2;
    return h("div.lt3-stack-3",
      h("div.lt3-row-2", c.sourceBadge(source)),
      rows.length ? c.table([
        { key: "status", label: t("common.status"), width: "1%", render: (r) => c.statePill(mapStatus(r.status)) },
        { key: "mode", label: t("runs.mode"), width: "1%", render: (r) => c.pill(r.mode || r.execution_mode || "live") },
        { key: "name", label: t("common.name"), render: (r) => h("div", h("b", r.name || r.workflow_name || r.agent_id || r.workflow_id || r.id), h("div.lt3-faint", { style: { "font-family": "var(--lt3-font-mono)", "font-size": "var(--lt3-text-2xs)" } }, r.id || r.run_id || "")) },
        { key: "when", label: t("common.updated"), width: "1%", render: (r) => h("span.lt3-faint", { style: { "white-space": "nowrap" } }, fmt(r.updated_at || r.created_at || r.completed_at)) },
        { key: "timeline", label: t("runs.progress"), render: (r) => miniTimeline(ctx2, r.timeline || []) },
        { key: "act", label: "", width: "1%", render: (r) => ACTIVE.has(String(r.status || "").toLowerCase())
          ? h("button.lt3-btn.lt3-btn--danger.lt3-btn--sm", { on: { click: () => cancelRun(kind, r.id || r.run_id) } }, c.icon("player-stop"), t("common.stop"))
          : null },
      ], rows.slice(0, 40)) : c.emptyState({ icon: "history-off", title: kind === "agent" ? t("runs.agentRuns") : t("runs.workflowRuns"), body: t("common.none") }),
    );
  }

  function approvalList(ctx2, workflowRuns, pending, source) {
    const workflowApprovals = workflowRuns.filter((r) => String(r.status || "").toLowerCase() === "awaiting_approval");
    const permissionRows = Object.entries(pending.pending || {}).map(([token, rec]) => ({ token, ...rec }));
    const nodes = [];
    nodes.push(h("div.lt3-row-2", c.sourceBadge(source)));
    if (workflowApprovals.length) {
      nodes.push(...workflowApprovals.map((run) => c.card(h("div.lt3-stack-3",
        h("div.lt3-row", { style: { "justify-content": "space-between" } },
          h("div", h("b", run.name || run.workflow_name || run.workflow_id), h("div.lt3-faint", t("runs.approvalPaused")), run.pause?.node ? h("div.lt3-faint", run.pause.node) : null),
          c.statePill("pending"),
        ),
        miniTimeline(ctx2, run.timeline || []),
        h("div.lt3-row-2",
          h("button.lt3-btn.lt3-btn--primary.lt3-btn--sm", { on: { click: () => decideWorkflow(run.id || run.run_id, true) } }, icon("circle-check"), t("common.approve")),
          h("button.lt3-btn.lt3-btn--ghost.lt3-btn--sm", { on: { click: () => decideWorkflow(run.id || run.run_id, false) } }, icon("circle-x"), t("common.deny")),
        ),
      ), { flat: true })));
    }
    if (permissionRows.length) {
      nodes.push(...permissionRows.map((rec) => c.card(h("div.lt3-stack-3",
        h("div", h("b", rec.action_label || rec.action || "permission"), h("div.lt3-faint", rec.path || rec.token), h("div.lt3-faint", { style: { "font-family": "var(--lt3-font-mono)", "font-size": "var(--lt3-text-2xs)" } }, rec.token)),
        h("div.lt3-row-2",
          h("button.lt3-btn.lt3-btn--primary.lt3-btn--sm", { on: { click: () => decidePermission(rec.token, true) } }, icon("circle-check"), t("common.approve")),
          h("button.lt3-btn.lt3-btn--ghost.lt3-btn--sm", { on: { click: () => decidePermission(rec.token, false) } }, icon("circle-x"), t("common.deny")),
        ),
      ), { flat: true })));
    }
    if (nodes.length === 1) nodes.push(c.emptyState({ icon: "circle-check", title: t("runs.approvals"), body: t("common.none") }));
    return h("div.lt3-stack-3", nodes);
  }

  function miniTimeline(ctx2, timeline) {
    const { h, c } = ctx2;
    if (!timeline.length) return h("span.lt3-faint", t("common.none"));
    return h("div.lt3-stack-2", timeline.slice(-3).map((item) =>
      h("div.lt3-row-2", c.statePill(mapStatus(item.status || item.event)), h("span.lt3-faint", item.event || item.message || item.step || "event"))));
  }

  async function cancelRun(kind, runId) {
    if (!runId) return;
    const res = kind === "agent" ? await api.stopAgentRun(runId) : await api.stopWorkflowRun(runId);
    toast(resultText(res, t("runs.cancelled")), res.ok ? "ok" : "err");
    load();
  }
  async function decideWorkflow(runId, approved) {
    const res = await api.resumeWorkflowRun(runId, approved);
    toast(resultText(res, t("runs.decided")), res.ok ? "ok" : "err");
    load();
  }
  async function decidePermission(token, approved) {
    const res = approved ? await api.approvePermission(token) : await api.denyPermission(token);
    toast(resultText(res, t("runs.decided")), res.ok ? "ok" : "err");
    load();
  }
}

function mapStatus(status) {
  const s = String(status || "").toLowerCase();
  if (s === "ok" || s === "completed" || s === "success" || s === "resumed") return "ready";
  if (s === "failed" || s === "error" || s === "denied" || s === "rejected") return "failed";
  if (s === "running" || s === "queued" || s === "in_progress" || s === "cancelling") return "active";
  if (s === "awaiting_approval" || s === "pending") return "pending";
  if (s === "cancelled" || s === "interrupted") return "warn";
  return s || "idle";
}

function fmt(ts) {
  if (!ts) return "—";
  try { return new Date(ts).toLocaleString(); } catch { return String(ts); }
}

function resultText(res, okText) {
  if (res && res.ok) return okText;
  const data = (res && res.data) || {};
  return String(data.detail || data.error || res?.error || t("common.unavailable"));
}
