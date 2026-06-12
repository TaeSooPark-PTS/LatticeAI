import { t } from "../core/i18n.880e1fec.js";

export async function render(ctx) {
  const { h, icon, api, c, toast } = ctx;
  const host = h("div.lt3-stack-6", c.loading({ lines: 5, block: true }));

  async function load() {
    const [snaps, timeline] = await Promise.all([api.snapshots(), api.timeMachine(80)]);
    const rows = normalize(snaps.data);
    host.replaceChildren(
      c.viewHeader({
        eyebrow: t("snapshots.eyebrow"),
        title: t("snapshots.title"),
        sub: t("snapshots.sub"),
        actions: [c.sourceBadge(snaps.source)],
      }),
      createPanel(),
      comparePanel(rows),
      snapshotTable(rows),
      timelinePanel(timeline),
    );
  }

  function createPanel() {
    const name = h("input.lt3-input", { type: "text", placeholder: t("snapshots.name"), "aria-label": t("snapshots.name") });
    return c.panel({
      title: t("snapshots.create"),
      children: h("div.lt3-row-2",
        name,
        h("button.lt3-btn.lt3-btn--primary", { on: { click: async () => {
          const res = await api.createSnapshot(name.value.trim() || t("snapshots.title"));
          toast(resultText(res, t("snapshots.created")), res.ok ? "ok" : "err");
          if (res.ok) { name.value = ""; load(); }
        } } }, icon("camera"), t("snapshots.create")),
      ),
    });
  }

  function comparePanel(rows) {
    const before = select(rows);
    const after = select(rows);
    const result = h("div");
    return c.panel({
      title: t("snapshots.compare"),
      children: h("div.lt3-stack-4",
        h("div.lt3-row-2", before, after, h("button.lt3-btn.lt3-btn--primary", { on: { click: async () => {
          if (!before.value || !after.value) return;
          result.replaceChildren(c.loading({ lines: 2 }));
          const res = await api.compareSnapshots(before.value, after.value);
          const d = res.data || {};
          result.replaceChildren(res.ok
            ? h("dl.lt3-keyval",
                h("dt", "nodes_added"), h("dd", String(d.summary?.nodes_added ?? 0)),
                h("dt", "nodes_removed"), h("dd", String(d.summary?.nodes_removed ?? 0)),
                h("dt", "edges_added"), h("dd", String(d.summary?.edges_added ?? 0)),
                h("dt", "edges_removed"), h("dd", String(d.summary?.edges_removed ?? 0)),
              )
            : c.banner(resultText(res, t("common.unavailable")), "err"));
        } } }, icon("git-compare"), t("snapshots.compare"))),
        result,
      ),
    });
  }

  function snapshotTable(rows) {
    if (!rows.length) return c.emptyState({ icon: "history", title: t("snapshots.title"), body: t("common.none") });
    return c.panel({
      title: t("snapshots.title"),
      children: c.table([
        { key: "name", label: t("common.name"), render: (r) => h("div", h("b", r.name || r.id), h("div.lt3-faint", { style: { "font-family": "var(--lt3-font-mono)", "font-size": "var(--lt3-text-2xs)" } }, r.id)) },
        { key: "created", label: t("common.created"), width: "1%", render: (r) => h("span.lt3-faint", { style: { "white-space": "nowrap" } }, fmt(r.created_at)) },
        { key: "nodes", label: "nodes", width: "1%", render: (r) => String(r.node_count ?? 0) },
        { key: "edges", label: "edges", width: "1%", render: (r) => String(r.edge_count ?? 0) },
        { key: "actions", label: "", width: "1%", render: (r) => h("div.lt3-row-2",
          h("button.lt3-btn.lt3-btn--subtle.lt3-btn--sm", { on: { click: () => exportSnapshot(r.id) } }, icon("download"), t("snapshots.export")),
          h("button.lt3-btn.lt3-btn--ghost.lt3-btn--sm", { on: { click: () => restoreSnapshot(r.id) } }, icon("restore"), t("snapshots.restore")),
        ) },
      ], rows),
    });
  }

  function timelinePanel(res) {
    const events = Array.isArray(res.data?.events) ? res.data.events : [];
    return c.panel({
      title: t("snapshots.timeline"),
      actions: [c.sourceBadge(res.source)],
      children: events.length ? c.table([
        { key: "event", label: t("common.status"), render: (e) => h("span", e.event_type || e.area || "event") },
        { key: "area", label: t("common.type"), width: "1%", render: (e) => c.pill(e.area || "workspace") },
        { key: "when", label: t("common.created"), width: "1%", render: (e) => h("span.lt3-faint", { style: { "white-space": "nowrap" } }, fmt(e.timestamp)) },
      ], events.slice(0, 40)) : c.emptyState({ icon: "history-off", title: t("snapshots.timeline"), body: t("common.none") }),
    });
  }

  async function exportSnapshot(id) {
    const res = await api.snapshotExport(id);
    toast(resultText(res, res.data?.export_path || t("snapshots.export")), res.ok ? "ok" : "err");
  }
  async function restoreSnapshot(id) {
    const res = await api.snapshotRestore(id);
    toast(resultText(res, t("snapshots.restored")), res.ok ? "ok" : "err");
    if (res.ok) load();
  }

  await load();
  return host;
}

function select(rows) {
  const sel = document.createElement("select");
  sel.className = "lt3-select";
  sel.setAttribute("aria-label", t("snapshots.title"));
  for (const row of rows) {
    const opt = document.createElement("option");
    opt.value = row.id;
    opt.textContent = row.name || row.id;
    sel.append(opt);
  }
  return sel;
}

function normalize(data) {
  return Array.isArray(data?.snapshots) ? data.snapshots : [];
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
