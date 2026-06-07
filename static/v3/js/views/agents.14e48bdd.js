/* ============================================================================
 * View: Agents — the multi-agent runtime (roles, real runs, health).
 * Reads the AgentRuntime boundary (/agents/api/runtime/status): the canonical
 * role roster enriched with real run counts, the live recent-runs ledger, and
 * runtime health. Reports unavailable state when the runtime is unreachable.
 * ========================================================================== */

export async function render(ctx) {
  const { h, icon, c } = ctx;

  const statHost = h("div.lt3-statrow", c.loading({ lines: 1 }));
  const rosterHost = h("div", c.loading({ lines: 2, block: true }));
  const runsHost = h("div", c.loading({ lines: 4 }));
  const rosterSrc = h("span", c.sourceBadge("pending"));
  const runsSrc = h("span", c.sourceBadge("pending"));
  const healthSlot = h("span", c.sourceBadge("pending"));

  const root = h("div.lt3-stack-6",
    c.viewHeader({
      eyebrow: "Compute",
      title: "Agents",
      sub: "The multi-agent runtime: who plans, who builds, who reviews — and how work hands off between them. Every run stays local to this workspace.",
      actions: [healthSlot],
    }),
    statHost,
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

  hydrate(ctx, { statHost, rosterHost, runsHost, rosterSrc, runsSrc, healthSlot });
  return root;
}

async function hydrate(ctx, hosts) {
  const { h, icon, c } = ctx;
  const { statHost, rosterHost, runsHost, rosterSrc, runsSrc, healthSlot } = hosts;

  const res = await ctx.api.agentRuntime();
  const data = res.data || {};
  const agents = normalize(data.agents);
  const runtime = data.runtime || {};
  const health = data.health || { status: "unknown" };
  const runs = Array.isArray(data.runs) ? data.runs : [];
  const byId = new Map(agents.map((a) => [a.id, a.name]));

  rosterSrc.replaceChildren(c.sourceBadge(res.source));
  runsSrc.replaceChildren(c.sourceBadge(res.source));
  healthSlot.replaceChildren(
    c.statePill(health.status === "ok" ? "ready" : health.status === "degraded" ? "warn" : "idle"),
  );

  // ── Stat row (real runtime counts) ────────────────────────────────────
  const available = agents.filter((a) => isAvailable(a.state)).length;
  const totalRuns = Number(runtime.total_runs) || runs.length;
  const handoffs = agents.reduce((sum, a) => sum + a.handoffs.length, 0);
  statHost.replaceChildren(
    c.stat({ label: "Agents", value: c.fmtNum(agents.length), icon: "robot" }),
    c.stat({ label: "Available", value: c.fmtNum(available), icon: "circle-check" }),
    c.stat({ label: "Total runs", value: c.fmtNum(totalRuns), icon: "player-play" }),
    c.stat({ label: "Handoffs", value: c.fmtNum(handoffs), icon: "arrows-exchange" }),
  );

  // ── Roster grid ───────────────────────────────────────────────────────
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

  // ── Recent runs ledger (REAL runs from the runtime) ───────────────────
  const rows = runs.map((r) => ({
    agent: byId.get(r.agent_id) || shortId(r.agent_id),
    status: mapStatus(r.status),
    time: fmtTime(r.created_at || r.completed_at),
    note: runNote(r),
  }));
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
      ],
      rows,
      { empty: c.emptyState({ icon: "history-off", title: "No runs yet", body: "Agent runs recorded by the runtime will appear here." }) },
    ),
  );
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

const AVAILABLE_STATES = new Set(["available", "ready", "active", "ok", "idle"]);
function isAvailable(state) {
  return AVAILABLE_STATES.has(String(state).toLowerCase());
}

// Map orchestrator run statuses onto the shared state-pill vocabulary.
function mapStatus(status) {
  const s = String(status || "").toLowerCase();
  if (s === "ok" || s === "retried_ok") return "ready";
  if (s === "failed" || s === "rejected") return "failed";
  if (s === "running" || s === "in_progress") return "active";
  return s || "idle";
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
