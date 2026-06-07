/* ============================================================================
 * View: Audit Logs — Administration · activity and access trail.
 * Reads /admin/audit (live) and renders unavailable state when it cannot load.
 * Severity filter narrows the rendered events; a compact stat row summarizes
 * actors, volume and risk at a glance.
 * ========================================================================== */

import { timeAgo } from "../core/dom.js";

const SEVERITY = {
  warning: { variant: "warn", label: "Warning", icon: "alert-triangle" },
  notice: { variant: "info", label: "Notice", icon: "info-circle" },
  informational: { variant: "", label: "Informational", icon: "point" },
};
function severityMeta(s) {
  return SEVERITY[String(s || "").toLowerCase()] || { variant: "", label: titleCase(s) || "Event", icon: "point" };
}

const FILTERS = [
  { key: "all", label: "All" },
  { key: "informational", label: "Informational" },
  { key: "notice", label: "Notice" },
  { key: "warning", label: "Warning" },
];

export async function render(ctx) {
  const { h, icon, api, c } = ctx;

  const state = { events: [], source: "pending", filter: "all", loaded: false };

  const srcSlot = h("span", c.sourceBadge("pending"));
  const filterHost = h("div", buildTabs());
  const statHost = h("div.lt3-statrow", c.loading({ lines: 1 }));
  const tableHost = h("div", c.loading({ lines: 6 }));

  const root = h("div.lt3-stack-6",
    c.viewHeader({
      eyebrow: "Administration",
      title: "Audit Logs",
      sub: "Activity and access trail",
      actions: [
        srcSlot,
        h("button.lt3-btn.lt3-btn--ghost", {
          on: { click: () => ctx.toast("Audit export is not available in this build (SIEM export is an Enterprise feature).", "warn") },
        }, icon("download"), "Export"),
      ],
    }),
    statHost,
    c.panel({
      eyebrow: "Trail",
      title: "Recent events",
      head: h("div.lt3-row", { style: { "justify-content": "space-between", flex: "1 1 auto", gap: "var(--lt3-space-3)" } },
        h("div", h("div.lt3-eyebrow", "Trail"), h("h3.lt3-panel__title", "Recent events")),
        filterHost,
      ),
      children: tableHost,
    }),
  );

  function buildTabs() {
    return c.tabs(FILTERS, state.filter, (key) => {
      state.filter = key;
      filterHost.replaceChildren(buildTabs());
      renderTable();
    });
  }

  function visibleEvents() {
    if (state.filter === "all") return state.events;
    return state.events.filter((e) => String(e.severity || "").toLowerCase() === state.filter);
  }

  function renderStats() {
    const events = state.events;
    const actors = new Set(events.map((e) => e.actor).filter(Boolean)).size;
    const startOfDay = new Date(); startOfDay.setHours(0, 0, 0, 0);
    const today = events.filter((e) => {
      const t = e.ts ? new Date(e.ts).getTime() : NaN;
      return !Number.isNaN(t) && t >= startOfDay.getTime();
    }).length;
    const high = events.filter((e) => ["warning", "high", "critical"].includes(String(e.severity || "").toLowerCase())).length;
    statHost.replaceChildren(
      c.stat({ label: "Total events", value: c.fmtNum(events.length), icon: "list-details" }),
      c.stat({ label: "Actors", value: c.fmtNum(actors), icon: "users" }),
      c.stat({ label: "Today", value: c.fmtNum(today), icon: "calendar-event" }),
      c.stat({ label: "High-severity", value: c.fmtNum(high), icon: "shield-exclamation" }),
    );
  }

  function renderTable() {
    const rows = visibleEvents();
    if (!rows.length) {
      tableHost.replaceChildren(state.loaded
        ? c.emptyState({
            icon: "history-off",
            title: state.filter === "all" ? "No audit events" : "No matching events",
            body: state.filter === "all"
              ? "Activity will appear here as users act in the workspace."
              : "No events match this severity. Try a broader filter.",
            action: state.filter === "all" ? null : h("button.lt3-btn.lt3-btn--subtle.lt3-btn--sm", {
              on: { click: () => { state.filter = "all"; filterHost.replaceChildren(buildTabs()); renderTable(); } },
            }, icon("filter-off"), "Clear filter"),
          })
        : c.loading({ lines: 6 }));
      return;
    }
    tableHost.replaceChildren(c.table(columns(ctx), rows));
  }

  async function load() {
    const res = await api.adminAudit();
    state.events = normalize(res.data);
    state.source = res.source;
    state.loaded = true;
    srcSlot.replaceChildren(c.sourceBadge(res.source));
    renderStats();
    renderTable();
  }

  load();
  return root;
}

/* ── table ───────────────────────────────────────────────────────────────── */
function columns({ h, icon, c }) {
  return [
    {
      key: "ts", label: "Time", width: "1%",
      render: (e) => h("span.lt3-mono.lt3-faint", { style: { "white-space": "nowrap", "font-size": "var(--lt3-text-2xs)" } },
        e.ts ? timeAgo(e.ts) : "—"),
    },
    {
      key: "actor", label: "Actor",
      render: (e) => h("div.lt3-row-2",
        h("span.lt3-avatar", { style: { width: "26px", height: "26px" } }, initials(e.actor)),
        h("span", { style: { "font-size": "var(--lt3-text-sm)", "white-space": "nowrap" } }, e.actor || "system"),
      ),
    },
    {
      key: "action", label: "Action", width: "1%",
      render: (e) => h("span.lt3-pill.lt3-mono", { style: { "white-space": "nowrap" } }, e.action || "event"),
    },
    {
      key: "target", label: "Target",
      render: (e) => h("span.lt3-faint", { style: { "font-size": "var(--lt3-text-sm)" } }, e.target || "—"),
    },
    {
      key: "severity", label: "Severity", width: "1%",
      render: (e) => {
        const m = severityMeta(e.severity);
        return c.pill(m.label, m.variant, { dot: true });
      },
    },
  ];
}

/* ── helpers ─────────────────────────────────────────────────────────────── */
function normalize(data) {
  const list = Array.isArray(data) ? data
    : Array.isArray(data && data.recent_events) ? data.recent_events
    : Array.isArray(data && data.events) ? data.events
    : [];
  return list.map((e) => ({
    ts: e.ts || e.timestamp || e.time || null,
    actor: e.actor || e.user || e.email || "system",
    action: e.action || e.event || "event",
    target: e.target || e.resource || "",
    severity: e.severity || e.level || "informational",
  }));
}

function initials(name) {
  const s = String(name || "·").trim();
  if (!s || s === "system") return "SY";
  const at = s.indexOf("@");
  const base = at > 0 ? s.slice(0, at) : s;
  const parts = base.split(/[\s._-]+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return base.slice(0, 2).toUpperCase();
}

function titleCase(s) {
  s = String(s || "").trim();
  return s ? s[0].toUpperCase() + s.slice(1).toLowerCase() : "";
}
