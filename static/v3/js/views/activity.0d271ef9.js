import { t } from "../core/i18n.880e1fec.js";

export async function render(ctx) {
  const { h, api, c } = ctx;
  const feedHost = h("div", c.loading({ lines: 4 }));
  const presenceHost = h("div", c.loading({ lines: 2 }));
  const timelineHost = h("div", c.loading({ lines: 4 }));

  const root = h("div.lt3-stack-6",
    c.viewHeader({ eyebrow: t("activity.eyebrow"), title: t("activity.title"), sub: t("activity.sub") }),
    c.panel({ title: t("activity.feed"), children: feedHost }),
    c.panel({ title: t("activity.presence"), children: presenceHost }),
    c.panel({ title: t("activity.timeMachine"), children: timelineHost }),
  );

  await load();
  wireLiveFeed();
  return root;

  async function load() {
    const [feed, presence, timeline] = await Promise.all([api.realtimeFeed(80), api.presence(), api.timeMachine(80)]);
    feedHost.replaceChildren(listEvents(ctx, feed.data?.events || [], feed.source));
    presenceHost.replaceChildren(listPresence(ctx, presence.data?.presence || [], presence.source));
    timelineHost.replaceChildren(listEvents(ctx, timeline.data?.events || [], timeline.source));
  }

  function wireLiveFeed() {
    if (!window.EventSource) return;
    try {
      const stream = new EventSource("/realtime/stream");
      stream.onmessage = () => load();
      setTimeout(() => stream.close(), 120000);
    } catch {}
  }
}

function listEvents(ctx, events, source) {
  const { h, c } = ctx;
  return h("div.lt3-stack-3",
    h("div.lt3-row-2", c.sourceBadge(source)),
    events.length ? c.table([
      { key: "event", label: t("common.status"), render: (e) => h("div", h("b", e.event_type || e.type || e.area || "event"), h("div.lt3-faint", { style: { "font-size": "var(--lt3-text-2xs)" } }, e.payload?.run_id || e.payload?.workflow_id || e.id || "")) },
      { key: "area", label: t("common.type"), width: "1%", render: (e) => c.pill(e.area || e.kind || "system") },
      { key: "when", label: t("common.created"), width: "1%", render: (e) => h("span.lt3-faint", { style: { "white-space": "nowrap" } }, fmt(e.timestamp || e.created_at || e.at)) },
    ], events.slice(0, 50)) : c.emptyState({ icon: "activity", title: t("activity.feed"), body: t("common.none") }),
  );
}

function listPresence(ctx, rows, source) {
  const { h, c } = ctx;
  return h("div.lt3-stack-3",
    h("div.lt3-row-2", c.sourceBadge(source)),
    rows.length ? c.table([
      { key: "user", label: t("account.email"), render: (p) => p.user || p.email || p.client_id || "local" },
      { key: "workspace", label: "workspace_id", render: (p) => h("span.lt3-mono", p.workspace_id || "personal") },
      { key: "when", label: t("common.updated"), width: "1%", render: (p) => fmt(p.last_seen || p.joined_at) },
    ], rows) : c.emptyState({ icon: "users", title: t("activity.presence"), body: t("common.none") }),
  );
}

function fmt(ts) {
  if (!ts) return "—";
  try {
    const d = typeof ts === "number" ? new Date(ts * 1000) : new Date(ts);
    return Number.isNaN(d.getTime()) ? String(ts) : d.toLocaleString();
  } catch { return String(ts); }
}
