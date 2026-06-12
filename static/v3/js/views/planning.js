/* ============================================================================
 * View: Planning — autonomous goal-based planning (goal → plan → execute →
 * review → replan). Drives the real AgentRuntime (/agents/api/run) and renders
 * the generated plan, execution, review, and replanning (retry) decisions, with
 * inspect / replay over recent runs. Synchronous runs are terminal — surfaced
 * honestly rather than faking a pause/stop.
 * ========================================================================== */

const FLOW = ["Goal", "Plan", "Execute", "Review", "Replan"];

export async function render(ctx) {
  const { h, c } = ctx;

  const goalInput = h("textarea.lt3-textarea", { rows: "2", placeholder: "Describe a goal — the planner decomposes it, the executor runs it, the reviewer approves or requests a replan.", "aria-label": "Goal" });
  const runBtn = h("button.lt3-btn.lt3-btn--primary", { on: { click: runGoal } }, c.icon("player-play"), "Generate plan & run");
  const resultHost = h("div");
  const runsHost = h("div", c.loading({ lines: 3 }));
  const runsSrc = h("span", c.sourceBadge("pending"));

  const root = h("div.lt3-stack-6",
    c.viewHeader({
      eyebrow: "Compute",
      title: "Autonomous Planning",
      sub: "Set a goal; the multi-agent runtime plans, executes, reviews, and replans on failure. Every plan, decision, and retry is inspectable and replayable.",
    }),
    flowLegend(ctx),
    c.panel({
      title: "New plan", sub: "Runs locally through planner → executor → reviewer with bounded retries.",
      children: h("div.lt3-stack-3", goalInput, h("div.lt3-row-2", runBtn,
        h("span.lt3-faint", { style: { "font-size": "var(--lt3-text-xs)" } }, "Safeguards: bounded retries, redacted context, replayable timeline."))),
    }),
    resultHost,
    c.panel({
      head: h("div.lt3-row", { style: { "justify-content": "space-between", width: "100%" } },
        h("div", h("div.lt3-eyebrow", "History"), h("h3.lt3-panel__title", "Recent plans & runs")), runsSrc),
      children: runsHost,
    }),
  );

  loadRuns();
  return root;

  function flowLegend(ctx2) {
    return h("div.lt3-cluster", { style: { gap: "var(--lt3-space-2)" } }, FLOW.map((step, i) =>
      h("div.lt3-row-2", { style: { gap: "var(--lt3-space-2)" } },
        c.pill(step, i === FLOW.length - 1 ? "warn" : "info"),
        i < FLOW.length - 1 ? c.icon("arrow-right") : null,
      )));
  }

  async function runGoal() {
    const goal = goalInput.value.trim();
    if (!goal) { ctx.toast("Enter a goal first", "info"); return; }
    runBtn.disabled = true;
    resultHost.replaceChildren(c.panel({ title: "Planning…", children: c.loading({ lines: 4, block: true }) }));
    const res = await ctx.api.runAgent(goal, ["planner", "executor", "reviewer"]);
    runBtn.disabled = false;
    if (!res || !res.ok || !res.data) {
      resultHost.replaceChildren(c.banner("Planning is unavailable — start the local server and load a model.", "warn"));
      return;
    }
    if (res.data.accepted && res.data.run) {
      resultHost.replaceChildren(renderResult(ctx, res.data.run));
      pollRun(res.data.run.id || res.data.run.run_id);
    } else {
      resultHost.replaceChildren(renderResult(ctx, res.data.result || res.data));
    }
    loadRuns();
  }

  async function pollRun(runId) {
    if (!runId) return;
    for (let i = 0; i < 80; i += 1) {
      await sleep(i < 10 ? 400 : 1200);
      const res = await ctx.api.agentRunDetail(runId);
      const run = res && res.data && res.data.run;
      if (!res || !res.ok || !run) return;
      resultHost.replaceChildren(renderResult(ctx, run));
      loadRuns();
      if (!["queued", "running", "in_progress", "cancelling"].includes(String(run.status || "").toLowerCase())) return;
    }
  }

  async function loadRuns() {
    const res = await ctx.api.agentRuntime();
    runsSrc.replaceChildren(c.sourceBadge(res.source));
    const runs = (res.data && res.data.runs) || [];
    if (!runs.length) {
      runsHost.replaceChildren(c.emptyState({ icon: "history-off", title: "No plans yet", body: "Generated plans and their runs will appear here." }));
      return;
    }
    runsHost.replaceChildren(c.table(
      [
        { key: "status", label: "Status", width: "1%", render: (r) => c.statePill(mapStatus(r.status)) },
        { key: "goal", label: "Goal / output", render: (r) => h("span.lt3-muted", trunc(r.output || r.input || r.agent_id, 90)) },
        { key: "retries", label: "Retries", width: "1%", render: (r) => h("span.lt3-faint", String(r.retries ?? (r.retry_history ? r.retry_history.length : 0))) },
        { key: "act", label: "", width: "1%", render: (r) => h("button.lt3-btn.lt3-btn--ghost.lt3-btn--sm", { on: { click: () => replay(r) } }, c.icon("player-track-next"), "Replay") },
      ],
      runs.slice(0, 20),
    ));
  }

  async function replay(run) {
    const id = run.id || run.run_id;
    if (!id) { ctx.toast("Run id unavailable", "info"); return; }
    const res = await ctx.api.agentRunReplay(id);
    if (res && res.ok && res.data) {
      resultHost.replaceChildren(c.panel({ title: `Replay · ${id}`, children: timeline(ctx, (res.data.replay && res.data.replay.timeline) || res.data.replay || []) }));
      resultHost.scrollIntoView({ behavior: "smooth", block: "start" });
    } else {
      ctx.toast("Replay unavailable", "err");
    }
  }
}

function renderResult(ctx, result) {
  const { h, c } = ctx;
  const plan = result.plan || [];
  const review = result.review || result.plan_review || {};
  const retries = result.retry_history || [];
  const status = mapStatus(result.status);
  const ok = status === "ready" || (review.outcome || "").toLowerCase() === "approve" || (review.verdict || "").toLowerCase() === "pass";
  return c.panel({
    head: h("div.lt3-row", { style: { "justify-content": "space-between", width: "100%" } },
      h("div", h("div.lt3-eyebrow", "Result"), h("h3.lt3-panel__title", "Plan & execution")),
      c.statePill(ok ? "ready" : status || "warn")),
    children: h("div.lt3-stack-3",
      h("div",
        h("div.lt3-eyebrow", c.icon("list-check"), "Plan"),
        plan.length
          ? h("ol", { style: { margin: "var(--lt3-space-2) 0 0", "padding-left": "1.2em" } }, plan.map((s) =>
              h("li", { style: { "margin-bottom": "4px" } }, h("span", s.description || s.name || `Step`), " ", c.statePill(s.status || "planned"))))
          : h("p.lt3-faint", { style: { margin: 0 } }, "No plan steps."),
      ),
      h("div",
        h("div.lt3-eyebrow", c.icon("checkup-list"), "Review"),
        h("p.lt3-muted", { style: { margin: "var(--lt3-space-2) 0 0" } }, review.reason || "—"),
      ),
      retries.length
        ? h("div",
            h("div.lt3-eyebrow", c.icon("refresh-alert"), `Replanning (${retries.length})`),
            h("div.lt3-stack-2", retries.map((r) => c.card(h("div",
              h("b", `Retry #${r.retry}`), h("p.lt3-muted", { style: { margin: 0, "font-size": "var(--lt3-text-sm)" } }, r.reason || "")), { flat: true }))),
          )
        : null,
      result.timeline ? h("details", h("summary.lt3-faint", { style: { cursor: "pointer" } }, "Timeline"), timeline(ctx, result.timeline)) : null,
    ),
  });
}

function timeline(ctx, events) {
  const { h, c } = ctx;
  const list = Array.isArray(events) ? events : [];
  if (!list.length) return h("p.lt3-faint", { style: { margin: 0 } }, "No timeline events.");
  return h("div.lt3-stack-2", { style: { "margin-top": "var(--lt3-space-2)" } }, list.slice(0, 60).map((e) =>
    h("div.lt3-row-2", { style: { "font-size": "var(--lt3-text-xs)" } },
      c.pill(e.event || "event", "", { dot: true }),
      h("span.lt3-faint", e.role || e.from || ""),
      e.to ? c.icon("arrow-right") : null,
      e.to ? h("span.lt3-faint", e.to) : null,
    )));
}

function mapStatus(s) {
  const v = String(s || "").toLowerCase();
  if (v === "ok" || v === "retried_ok") return "ready";
  if (v === "failed" || v === "rejected") return "failed";
  if (v === "running" || v === "in_progress" || v === "queued" || v === "cancelling") return "active";
  if (v === "cancelled" || v === "interrupted") return "warn";
  return v || "idle";
}

function trunc(s, n) { s = String(s || ""); return s.length > n ? s.slice(0, n) + "…" : s; }
function sleep(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }
