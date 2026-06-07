/* ============================================================================
 * View: Agents — the multi-agent runtime (roles, real runs, health).
 * Reads the AgentRuntime boundary (/agents/api/runtime/status): the canonical
 * role roster enriched with real run counts, the live recent-runs ledger, and
 * runtime health. Reports unavailable state when the runtime is unreachable.
 * ========================================================================== */

export async function render(ctx) {
  const { h, icon, c } = ctx;

  const statHost = h("div.lt3-statrow", c.loading({ lines: 1 }));
  const registryHost = h("div", c.loading({ lines: 3, block: true }));
  const rosterHost = h("div", c.loading({ lines: 2, block: true }));
  const runsHost = h("div", c.loading({ lines: 4 }));
  const registrySrc = h("span", c.sourceBadge("pending"));
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

  hydrate(ctx, { statHost, rosterHost, runsHost, rosterSrc, runsSrc, healthSlot });
  loadRegistry(ctx, { registryHost, registrySrc });
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
