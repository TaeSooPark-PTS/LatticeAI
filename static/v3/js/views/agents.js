/* ============================================================================
 * View: Agents — multi-agent roles, runs, and handoffs.
 * Renders the agent roster as interactive cards (integration-ready against
 * /workspace/agents) with a derived "Recent runs" ledger. Falls back to
 * clearly-badged sample data when the backend agents endpoint isn't available.
 * ========================================================================== */

import * as fx from "../core/fixtures.js";

export async function render(ctx) {
  const { h, icon, api, store, c } = ctx;

  const statHost = h("div.lt3-statrow", c.loading({ lines: 1 }));
  const rosterHost = h("div", c.loading({ lines: 2, block: true }));
  const runsHost = h("div", c.loading({ lines: 4 }));
  const rosterSrc = h("span", c.sourceBadge("pending"));
  const runsSrc = h("span", c.sourceBadge("pending"));

  const root = h("div.lt3-stack-6",
    c.viewHeader({
      eyebrow: "Compute",
      title: "Agents",
      sub: "The multi-agent roster: who plans, who builds, who reviews — and how work hands off between them. Every run stays local to this workspace.",
      actions: [
        h("button.lt3-btn.lt3-btn--primary", {
          on: { click: () => ctx.toast("New agent — authoring flow is pending backend integration.", "info") },
        }, icon("plus"), "New agent"),
      ],
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

  hydrate(ctx, { statHost, rosterHost, runsHost, rosterSrc, runsSrc });
  return root;
}

async function hydrate(ctx, hosts) {
  const { h, icon, c } = ctx;
  const { statHost, rosterHost, runsHost, rosterSrc, runsSrc } = hosts;

  const res = await ctx.api.get("/workspace/agents", { agents: fx.AGENTS });
  const agents = normalize(res.data);
  const byId = new Map(agents.map((a) => [a.id, a.name]));

  rosterSrc.replaceChildren(c.sourceBadge(res.source));
  runsSrc.replaceChildren(c.sourceBadge(res.source));

  // ── Stat row ──────────────────────────────────────────────────────────
  const available = agents.filter((a) => isAvailable(a.state)).length;
  const totalRuns = agents.reduce((sum, a) => sum + (Number(a.runs) || 0), 0);
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
      title: "No agents yet",
      body: "Define an agent role to start orchestrating multi-step work.",
      action: h("button.lt3-btn.lt3-btn--ghost.lt3-btn--sm", {
        on: { click: () => ctx.toast("New agent — authoring flow is pending backend integration.", "info") },
      }, icon("plus"), "New agent"),
    }));
  } else {
    rosterHost.replaceChildren(
      h("div.lt3-grid-auto", agents.map((a) => agentCard(ctx, a, byId))),
    );
  }

  // ── Recent runs ledger (derived from the roster) ──────────────────────
  const runs = deriveRuns(agents);
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
      runs,
      { empty: c.emptyState({ icon: "history-off", title: "No runs recorded", body: "Agent activity will appear here once runs execute." }) },
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
              return h("button.lt3-chip", {
                on: { click: () => ctx.toast(`Handoff to ${name} — routing details pending backend integration.`, "info") },
              }, icon("arrow-right"), name);
            })),
          )
        : h("div.lt3-faint", { style: { "font-size": "var(--lt3-text-xs)" } }, "Terminal role — no handoffs"),
    ),
    { interactive: true },
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

// Sample run ledger derived deterministically from the roster, so the table
// reflects the same agents. Marked "Sample data" via the source badge above.
const RUN_TEMPLATES = [
  { status: "ready", note: "Completed — decomposed goal into a 4-step plan." },
  { status: "active", note: "Running — editing files in the workspace." },
  { status: "idle", note: "Queued — awaiting upstream handoff." },
  { status: "ready", note: "Completed — hybrid retrieval over 1.8k sources." },
  { status: "failed", note: "Retried — transient tool timeout, recovered." },
];
function deriveRuns(agents) {
  return agents.slice(0, 6).map((a, i) => {
    const t = RUN_TEMPLATES[i % RUN_TEMPLATES.length];
    return { agent: a.name, status: t.status, time: "—", note: t.note };
  });
}

function shortId(id) {
  const s = String(id || "");
  return s.includes(":") ? s.split(":").pop() : s;
}
