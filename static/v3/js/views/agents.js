/* ============================================================================
 * View: Agents — the multi-agent runtime (roles, real runs, health).
 * Reads the AgentRuntime boundary (/agents/api/runtime/status): the canonical
 * role roster enriched with real run counts, the live recent-runs ledger, and
 * runtime health. Reports unavailable state when the runtime is unreachable.
 * Also drives runs directly: a goal + role selection → POST /agents/api/run →
 * a durable async run, live logs, final status/output, queue/status, and stop.
 * ========================================================================== */

import { timeAgo } from "../core/dom.js";

const DEFAULT_ROLES = ["planner", "executor", "reviewer"];

export async function render(ctx) {
  const { h, icon, c } = ctx;

  const runHost = h("div", c.loading({ lines: 3, block: true }));
  const statHost = h("div.lt3-statrow", c.loading({ lines: 1 }));
  const registryHost = h("div", c.loading({ lines: 3, block: true }));
  const rosterHost = h("div", c.loading({ lines: 2, block: true }));
  const runsHost = h("div", c.loading({ lines: 4 }));
  const registrySrc = h("span", c.sourceBadge("pending"));
  const rosterSrc = h("span", c.sourceBadge("pending"));
  const runsSrc = h("span", c.sourceBadge("pending"));
  const runSrc = h("span", c.sourceBadge("pending"));
  const healthSlot = h("span", c.sourceBadge("pending"));

  const root = h("div.lt3-stack-6",
    c.viewHeader({
      eyebrow: "Compute",
      title: "Agents",
      sub: "The multi-agent runtime: who plans, who builds, who reviews — and how work hands off between them. Every run stays local to this workspace.",
      actions: [healthSlot],
    }),
    c.panel({
      head: h("div.lt3-row", { style: { "justify-content": "space-between", width: "100%" } },
        h("div",
          h("div.lt3-eyebrow", "Run"),
          h("h3.lt3-panel__title", "Run agents"),
          h("p.lt3-panel__sub", "Give the pipeline a goal. Planner → executor → reviewer run locally with durable progress and cooperative cancellation."),
        ),
        runSrc,
      ),
      children: runHost,
    }),
    statHost,
    h("section",
      c.sectionHead("Agent Registry", registrySrc),
      registryHost,
    ),
    h("section",
      c.sectionHead("Agent roster", rosterSrc),
      rosterHost,
    ),
    c.panel({
      head: h("div.lt3-row", { style: { "justify-content": "space-between", width: "100%" } },
        h("div",
          h("div.lt3-eyebrow", "Activity"),
          h("h3.lt3-panel__title", "Recent runs"),
        ),
        runsSrc,
      ),
      children: runsHost,
    }),
  );

  // Shared run-console handles refresh + recent-runs + queue so a run can
  // re-hydrate counts and click-through to a run's logs.
  const runConsole = makeRunConsole(ctx, { runHost, runSrc, statHost, runsHost, runsSrc, healthSlot, rosterHost, rosterSrc });
  runConsole.hydrate();
  loadRegistry(ctx, { registryHost, registrySrc });
  return root;
}

/* ── Run console ─────────────────────────────────────────────────────────────
 * Owns the Run panel (goal + role chips + Run button + logs), the stat/queue
 * row, the recent-runs ledger, and the cross-links between them. A single
 * refresh() re-reads the runtime so counts and recent runs stay honest.
 * ========================================================================== */
function makeRunConsole(ctx, hosts) {
  const { h, icon, c } = ctx;
  const { runHost, runSrc, statHost, runsHost, runsSrc, healthSlot, rosterHost, rosterSrc } = hosts;

  // Run-panel controls (built once; logs region replaced per run).
  const goalInput = h("textarea.lt3-textarea", {
    placeholder: "What should the agents accomplish?",
    "aria-label": "Agent goal",
    rows: 3,
  });
  const logsHost = h("div");
  const runBtn = h("button.lt3-btn.lt3-btn--primary", { on: { click: () => trigger() } }, c.icon("player-play"), "Run agents");

  // Role chips — defaults seeded; replaced once runtime defaults arrive.
  let roleState = DEFAULT_ROLES.map((r) => ({ role: r, on: true }));
  const rolesHost = h("div.lt3-cluster");
  renderRoleChips();

  function renderRoleChips() {
    rolesHost.replaceChildren(
      ...roleState.map((entry, i) => h("button.lt3-chip", {
        type: "button",
        dataset: { active: String(entry.on) },
        "aria-pressed": String(entry.on),
        on: { click: () => { roleState[i].on = !roleState[i].on; renderRoleChips(); } },
      }, c.icon(entry.on ? "check" : "circle"), entry.role)),
    );
  }

  function selectedRoles() {
    return roleState.filter((e) => e.on).map((e) => e.role);
  }

  runHost.replaceChildren(
    h("div.lt3-stack-4",
      h("div.lt3-field",
        h("label", "Goal"),
        goalInput,
      ),
      h("div.lt3-field",
        h("label", "Roles"),
        rolesHost,
        h("span.lt3-faint", { style: { "font-size": "var(--lt3-text-xs)" } }, "Toggle which roles take part. Leave all on for the full pipeline."),
      ),
      h("div.lt3-row-2", runBtn),
      logsHost,
    ),
  );

  /* ── Trigger a run ─────────────────────────────────────────────────────── */
  async function trigger() {
    const goal = goalInput.value.trim();
    if (!goal) { ctx.toast("Enter a goal for the agents", "info"); return; }
    const roles = selectedRoles();
    if (!roles.length) { ctx.toast("Select at least one role", "info"); return; }

    runBtn.disabled = true;
    runBtn.replaceChildren(c.icon("loader-2"), "Starting…");
    runSrc.replaceChildren(c.sourceBadge("pending"));
    logsHost.replaceChildren(h("div", { style: { "margin-top": "var(--lt3-space-3)" } }, c.loading({ lines: 4 })));

    const res = await ctx.api.runAgent(goal, roles);
    runBtn.disabled = false;
    runBtn.replaceChildren(c.icon("player-play"), "Run agents");

    const data = (res && res.data) || {};
    if (!res || !res.ok) {
      const msg = data.detail || data.error || (res && res.error) || "Agent run unavailable";
      runSrc.replaceChildren(c.sourceBadge("unavailable"));
      logsHost.replaceChildren(h("div", { style: { "margin-top": "var(--lt3-space-3)" } },
        c.banner(String(msg), "err"),
      ));
      ctx.toast("Run failed", "err");
      return;
    }

    runSrc.replaceChildren(c.sourceBadge("live"));
    const run = data.run || {};
    const result = data.result || {};
    logsHost.replaceChildren(renderRunResult(run, result));
    if (data.accepted && (run.id || run.run_id)) {
      ctx.toast("Run queued", "ok");
      pollRun(run.id || run.run_id);
    } else {
      ctx.toast(`Run ${mapStatus(result.status) === "failed" ? "completed with failure" : "complete"}`, mapStatus(result.status) === "failed" ? "warn" : "ok");
    }

    // Refresh runtime so queue/total/recent-runs reflect this run.
    hydrate();
  }

  async function pollRun(runId) {
    for (let i = 0; i < 80; i += 1) {
      await sleep(i < 10 ? 400 : 1200);
      const res = await ctx.api.agentRunDetail(runId);
      const data = (res && res.data) || {};
      if (!res || !res.ok) return;
      const run = data.run || {};
      logsHost.replaceChildren(renderRunResult(run, run));
      hydrate();
      if (!isActiveStatus(run.status)) {
        const mapped = mapStatus(run.status);
        ctx.toast(`Run ${mapped === "failed" ? "completed with failure" : "finished"}`, mapped === "failed" ? "warn" : "ok");
        return;
      }
    }
  }

  /* ── Render a run's result as logs + summary ───────────────────────────── */
  function renderRunResult(run, result) {
    const runId = run.id || run.run_id || result.run_id || result.id;
    const status = mapStatus(result.status || run.status);
    const timeline = Array.isArray(result.timeline) ? result.timeline : (Array.isArray(run.timeline) ? run.timeline : []);
    const output = result.output != null ? String(result.output) : String(run.output_preview || "");
    const retries = Number(result.retries) || 0;
    const active = isActiveStatus(result.status || run.status);

    return h("div.lt3-stack-4", { style: { "margin-top": "var(--lt3-space-3)" } },
      h("hr.lt3-divider"),
      h("div.lt3-row", { style: { "justify-content": "space-between", "flex-wrap": "wrap", gap: "var(--lt3-space-2)" } },
        h("div.lt3-row-2",
          h("span.lt3-eyebrow", "Result"),
          c.statePill(status),
          retries ? c.pill(`${retries} ${retries === 1 ? "retry" : "retries"}`, "warn") : null,
          runId ? h("span.lt3-faint", { style: { "font-family": "var(--lt3-font-mono)", "font-size": "var(--lt3-text-2xs)" } }, shortId(runId)) : null,
        ),
        runId
          ? h("button.lt3-btn.lt3-btn--danger.lt3-btn--sm", {
              title: active ? "Stop this run" : "This run has already finished",
              on: { click: (e) => stopRun(runId, e.currentTarget) },
            }, c.icon("player-stop"), "Stop")
          : null,
      ),
      timeline.length
        ? renderTimeline(timeline)
        : h("p.lt3-faint", { style: { margin: 0, "font-size": "var(--lt3-text-sm)" } }, "No timeline entries were recorded for this run."),
      output
        ? h("div.lt3-stack-2",
            h("div.lt3-eyebrow", icon("file-text"), "Output"),
            h("pre.lt3-code", output),
          )
        : null,
    );
  }

  /* ── Timeline → readable log list ──────────────────────────────────────── */
  function renderTimeline(timeline) {
    return h("div.lt3-stack-2",
      h("div.lt3-eyebrow", icon("list-details"), "Logs"),
      h("div.lt3-timeline",
        timeline.map((entry) => {
          const label = entry.event || entry.message || entry.step || "event";
          const role = entry.role || entry.agent_id;
          const status = entry.status;
          const ts = entry.timestamp || entry.created_at || entry.time;
          return h("div.lt3-timeline__item",
            ts ? h("div.lt3-timeline__time", fmtTime(ts)) : null,
            h("div.lt3-timeline__body",
              h("div.lt3-row-2", { style: { "flex-wrap": "wrap" } },
                role ? h("b", { style: { "font-size": "var(--lt3-text-sm)" } }, shortId(role)) : null,
                h("span", String(label).replace(/_/g, " ")),
                status ? c.statePill(mapStatus(status)) : null,
              ),
              entry.detail || entry.note
                ? h("div.lt3-muted", { style: { "font-size": "var(--lt3-text-xs)", "margin-top": "var(--lt3-space-1)" } }, String(entry.detail || entry.note))
                : null,
            ),
          );
        }),
      ),
    );
  }

  /* ── Stop an (active) run — honest about synchronous no-op ─────────────── */
  async function stopRun(runId, btn) {
    if (btn) { btn.disabled = true; btn.replaceChildren(c.icon("loader-2"), "Stopping…"); }
    const res = await ctx.api.stopAgentRun(runId);
    if (btn) { btn.disabled = false; btn.replaceChildren(c.icon("player-stop"), "Stop"); }
    const data = (res && res.data) || {};
    if (!res || !res.ok) {
      ctx.toast(String(data.detail || data.error || "Stop unavailable"), "err");
      return;
    }
    if (data.stopped === false || data.stopped == null) {
      ctx.toast(String(data.reason || "Run already finished — nothing to stop"), "warn");
    } else {
      ctx.toast("Run stopped", "ok");
    }
    hydrate();
  }

  /* ── Load a recorded run's detail as logs ──────────────────────────────── */
  async function openRun(runId) {
    if (!runId) return;
    logsHost.replaceChildren(h("div", { style: { "margin-top": "var(--lt3-space-3)" } }, c.loading({ lines: 3 })));
    const res = await ctx.api.agentRunDetail(runId);
    const data = (res && res.data) || {};
    if (!res || !res.ok) {
      runSrc.replaceChildren(c.sourceBadge("unavailable"));
      logsHost.replaceChildren(h("div", { style: { "margin-top": "var(--lt3-space-3)" } },
        c.banner(String(data.detail || data.error || "Run detail unavailable"), "err"),
      ));
      return;
    }
    runSrc.replaceChildren(c.sourceBadge("live"));
    const run = data.run || data || {};
    const result = data.result || data || {};
    logsHost.replaceChildren(renderRunResult(run, result));
  }

  /* ── Refresh runtime → stats, queue, recent runs, roster, health ───────── */
  async function hydrate() {
    const res = await ctx.api.agentRuntime();
    const data = res.data || {};
    const agents = normalize(data.agents);
    const runtime = data.runtime || {};
    const health = data.health || { status: "unknown" };
    const runs = Array.isArray(data.runs) ? data.runs : [];
    const byId = new Map(agents.map((a) => [a.id, a.name]));

    // Seed role chips from the runtime's real default pipeline / roles (once).
    if (!roleState.__seeded) {
      const fromRuntime = Array.isArray(runtime.default_pipeline) && runtime.default_pipeline.length
        ? runtime.default_pipeline
        : (Array.isArray(data.roles) ? data.roles.map((r) => r.role).filter(Boolean) : []);
      if (fromRuntime.length) {
        roleState = fromRuntime.map((r) => ({ role: String(r), on: true }));
        roleState.__seeded = true;
        renderRoleChips();
      }
    }

    rosterSrc.replaceChildren(c.sourceBadge(res.source));
    runsSrc.replaceChildren(c.sourceBadge(res.source));
    healthSlot.replaceChildren(
      c.statePill(health.status === "ok" ? "ready" : health.status === "degraded" ? "warn" : "idle"),
    );

    // ── Stat row + queue/status (real runtime counts) ───────────────────
    const available = agents.filter((a) => isAvailable(a.state)).length;
    const totalRuns = Number(runtime.total_runs) || runs.length;
    const activeRuns = Number(runtime.active_runs) || 0;
    statHost.replaceChildren(
      c.stat({ label: "Agents", value: c.fmtNum(agents.length), icon: "robot" }),
      c.stat({ label: "Available", value: c.fmtNum(available), icon: "circle-check" }),
      c.stat({ label: "Total runs", value: c.fmtNum(totalRuns), icon: "player-play" }),
      c.stat({
        label: "Queue", value: c.fmtNum(activeRuns), icon: "clock-play",
        delta: activeRuns ? "active" : "idle", deltaDir: activeRuns ? "up" : undefined,
      }),
    );

    // ── Roster grid ─────────────────────────────────────────────────────
    if (!agents.length) {
      rosterHost.replaceChildren(c.emptyState({
        icon: "robot-off",
        title: "Runtime unavailable",
        body: "The agent runtime did not respond. Start the local server to see the roster.",
      }));
    } else {
      rosterHost.replaceChildren(
        h("div.lt3-grid-auto", agents.map((a) => agentCard(ctx, a, byId))),
      );
    }

    // ── Recent runs ledger (REAL runs; click → load logs) ───────────────
    if (!runs.length) {
      runsHost.replaceChildren(c.emptyState({ icon: "history-off", title: "No runs yet", body: "Trigger a run above — recorded runs appear here." }));
    } else {
      runsHost.replaceChildren(
        c.table(
          [
            { key: "agent", label: "Agent", render: (r) => h("div.lt3-row-2",
              h("span.lt3-avatar", { style: { width: "26px", height: "26px" } }, icon("robot")),
              h("b", { style: { "font-size": "var(--lt3-text-sm)" } }, r.agent),
            ) },
            { key: "status", label: "Status", width: "1%", render: (r) => c.statePill(r.status) },
            { key: "time", label: "Started", width: "1%", render: (r) => h("span.lt3-faint", { style: { "white-space": "nowrap", "font-family": "var(--lt3-font-mono)", "font-size": "var(--lt3-text-2xs)" } }, r.time) },
            { key: "note", label: "Note", render: (r) => h("span.lt3-muted", r.note) },
            { key: "open", label: "", width: "1%", render: (r) => r.id
              ? h("button.lt3-btn.lt3-btn--subtle.lt3-btn--sm", { on: { click: () => openRun(r.id) } }, icon("list-search"), "Logs")
              : null },
          ],
          runs.map((r) => ({
            id: r.id || r.run_id,
            agent: byId.get(r.agent_id) || shortId(r.agent_id),
            status: mapStatus(r.status),
            time: timeAgo(r.created_at || r.completed_at) || fmtTime(r.created_at || r.completed_at),
            note: runNote(r),
          })),
          { empty: c.emptyState({ icon: "history-off", title: "No runs yet", body: "Agent runs recorded by the runtime will appear here." }) },
        ),
      );
    }
  }

  return { hydrate, openRun };
}

async function loadRegistry(ctx, hosts) {
  const { h, c } = ctx;
  const { registryHost, registrySrc } = hosts;
  const [registryRes, capsRes] = await Promise.all([ctx.api.agentRegistry(), ctx.api.agentCapabilities()]);
  const agents = normalizeRegistry(registryRes.data);
  const caps = (capsRes.data && capsRes.data.capabilities) || {};
  registrySrc.replaceChildren(c.sourceBadge(registryRes.source === "live" || capsRes.source === "live" ? "live" : "unavailable"));

  const nameInput = h("input.lt3-input", { type: "text", placeholder: "Custom agent name", "aria-label": "Custom agent name" });
  const capsInput = h("input.lt3-input", { type: "text", placeholder: "capability-a, capability-b", "aria-label": "Custom agent capabilities" });
  const registerBtn = h("button.lt3-btn.lt3-btn--primary.lt3-btn--sm", { on: { click: register } }, c.icon("plus"), "Register");

  const capList = Object.keys(caps).sort();
  const body = h("div.lt3-stack-4",
    h("div.lt3-grid-2",
      h("div.lt3-field", h("label", "Name"), nameInput),
      h("div.lt3-field", h("label", "Capabilities"), capsInput),
    ),
    h("div.lt3-row-2", registerBtn,
      h("span.lt3-faint", { style: { "font-size": "var(--lt3-text-xs)" } }, "Custom agents persist in the local registry.")),
    capList.length
      ? h("div.lt3-cluster", capList.slice(0, 18).map((cap) => h("span.lt3-chip", c.icon("sparkles"), `${cap} (${caps[cap].length})`)))
      : h("p.lt3-faint", { style: { margin: 0 } }, "Capabilities appear here when the registry is live."),
    agents.length
      ? h("div.lt3-grid-auto", agents.map((agent) => registryCard(ctx, agent)))
      : c.emptyState({ icon: "robot-off", title: "Agent registry unavailable", body: "Start the local server to register and configure agents." }),
  );
  registryHost.replaceChildren(c.panel({ title: "Registry controls", sub: "Register, discover, and configure built-in or custom agents.", children: body }));

  async function register() {
    const name = nameInput.value.trim();
    if (!name) { ctx.toast("Enter an agent name", "info"); return; }
    const capabilities = capsInput.value.split(",").map((s) => s.trim()).filter(Boolean);
    registerBtn.disabled = true;
    const res = await ctx.api.registerAgent({ name, type: "custom", capabilities });
    registerBtn.disabled = false;
    if (res && res.ok) {
      ctx.toast(`Registered ${name}`, "ok");
      loadRegistry(ctx, hosts);
    } else {
      ctx.toast("Register unavailable", "err");
    }
  }
}

function registryCard(ctx, agent) {
  const { h, c } = ctx;
  return c.card(h("div.lt3-stack-3",
    h("div.lt3-row", { style: { "justify-content": "space-between", "align-items": "flex-start" } },
      h("div",
        h("b", agent.name),
        h("div.lt3-faint", { style: { "font-size": "var(--lt3-text-2xs)", "font-family": "var(--lt3-font-mono)" } }, agent.id),
      ),
      c.pill(agent.source === "builtin" ? "built-in" : "custom", agent.source === "builtin" ? "info" : "warn"),
    ),
    h("p.lt3-muted", { style: { "font-size": "var(--lt3-text-sm)", margin: 0 } }, agent.description || "No description."),
    h("div.lt3-cluster", [c.statePill(agent.enabled ? "ready" : "idle"), c.pill(agent.type), c.pill(`v${agent.version || "1.0.0"}`)]),
    agent.capabilities.length ? h("div.lt3-cluster", agent.capabilities.slice(0, 8).map((cap) => h("span.lt3-chip", cap))) : null,
    h("div.lt3-row-2",
      h("button.lt3-btn.lt3-btn--subtle.lt3-btn--sm", { on: { click: () => toggleAgent(ctx, agent) } }, c.icon(agent.enabled ? "toggle-right" : "toggle-left"), agent.enabled ? "Disable" : "Enable"),
      agent.removable ? h("button.lt3-btn.lt3-btn--ghost.lt3-btn--sm", { on: { click: () => removeAgent(ctx, agent) } }, c.icon("trash"), "Remove") : null,
    ),
  ), { interactive: false });
}

async function toggleAgent(ctx, agent) {
  const res = await ctx.api.updateAgent(agent.id, { config: agent.config || {}, enabled: !agent.enabled });
  ctx.toast(res && res.ok ? `${agent.name}: ${agent.enabled ? "disabled" : "enabled"}` : "Agent update unavailable", res && res.ok ? "ok" : "err");
  if (res && res.ok) ctx.navigate("agents");
}

async function removeAgent(ctx, agent) {
  const res = await ctx.api.removeAgent(agent.id);
  ctx.toast(res && res.ok ? `Removed ${agent.name}` : "Agent remove unavailable", res && res.ok ? "ok" : "err");
  if (res && res.ok) ctx.navigate("agents");
}

/* ── Agent card ──────────────────────────────────────────────────────────── */
function agentCard(ctx, agent, byId) {
  const { h, icon, c } = ctx;
  return c.card(
    h("div.lt3-stack-3",
      h("div.lt3-row", { style: { "justify-content": "space-between", "align-items": "flex-start" } },
        h("div.lt3-row-2",
          h("span.lt3-avatar", { style: { width: "40px", height: "40px", "border-radius": "var(--lt3-radius-md)" } }, icon("robot")),
          h("div",
            h("div", { style: { "font-weight": "var(--lt3-weight-semi)", "font-size": "var(--lt3-text-md)" } }, agent.name),
            h("div.lt3-faint", { style: { "font-size": "var(--lt3-text-2xs)", "font-family": "var(--lt3-font-mono)" } }, agent.id),
          ),
        ),
        c.statePill(agent.state),
      ),
      h("p.lt3-muted", { style: { "font-size": "var(--lt3-text-sm)", margin: "0" } }, agent.role),
      h("div.lt3-row-2", { style: { "font-size": "var(--lt3-text-xs)", color: "var(--muted)" } },
        icon("player-play"),
        h("b", { style: { color: "var(--text)" } }, c.fmtNum(agent.runs)),
        "runs",
      ),
      agent.handoffs.length
        ? h("div.lt3-stack-2",
            h("div.lt3-eyebrow", icon("arrows-exchange"), "Hands off to"),
            h("div.lt3-cluster", agent.handoffs.map((id) => {
              const name = byId.get(id) || shortId(id);
              return h("span.lt3-chip", icon("arrow-right"), name);
            })),
          )
        : h("div.lt3-faint", { style: { "font-size": "var(--lt3-text-xs)" } }, "Terminal role — no handoffs"),
    ),
    { interactive: false },
  );
}

/* ── helpers ─────────────────────────────────────────────────────────────── */
function normalize(data) {
  const list = Array.isArray(data) ? data : (data && Array.isArray(data.agents) ? data.agents : []);
  return list.map((a, i) => ({
    id: a.id || `agent:${i}`,
    name: a.name || a.id || `Agent ${i + 1}`,
    role: a.role || a.description || "No role description.",
    state: a.state || a.status || "idle",
    runs: a.runs ?? a.run_count ?? a.runs_count ?? 0,
    handoffs: Array.isArray(a.handoffs) ? a.handoffs
      : Array.isArray(a.relationships) ? a.relationships : [],
  }));
}

function normalizeRegistry(data) {
  const list = Array.isArray(data) ? data : (data && Array.isArray(data.agents) ? data.agents : []);
  return list.map((agent, i) => ({
    id: agent.id || `agent:${i}`,
    name: agent.name || agent.id || `Agent ${i + 1}`,
    type: agent.type || "custom",
    version: agent.version || "1.0.0",
    description: agent.description || "",
    capabilities: Array.isArray(agent.capabilities) ? agent.capabilities : [],
    source: agent.source || "user",
    enabled: agent.enabled !== false,
    removable: !!agent.removable,
    config: agent.config || {},
  }));
}

const AVAILABLE_STATES = new Set(["available", "ready", "active", "ok", "idle"]);
function isAvailable(state) {
  return AVAILABLE_STATES.has(String(state).toLowerCase());
}

// Map orchestrator run statuses onto the shared state-pill vocabulary.
function mapStatus(status) {
  const s = String(status || "").toLowerCase();
  if (s === "ok" || s === "retried_ok") return "ready";
  if (s === "failed" || s === "rejected") return "failed";
  if (s === "running" || s === "in_progress" || s === "queued" || s === "cancelling") return "active";
  if (s === "cancelled" || s === "interrupted") return "warn";
  return s || "idle";
}

// An active run is one that could (in principle) still be stopped.
const ACTIVE_STATES = new Set(["running", "in_progress", "queued", "pending", "active", "cancelling"]);
function isActiveStatus(status) {
  return ACTIVE_STATES.has(String(status || "").toLowerCase());
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function runNote(r) {
  const out = String(r.output || r.input || "").trim();
  if (out) return out.length > 96 ? out.slice(0, 96) + "…" : out;
  return `Run ${shortId(r.agent_id)} — ${r.status || "recorded"}`;
}

function fmtTime(ts) {
  if (!ts) return "—";
  try {
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return String(ts);
    return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  } catch { return String(ts); }
}

function shortId(id) {
  const s = String(id || "");
  return s.includes(":") ? s.split(":").pop() : s;
}
