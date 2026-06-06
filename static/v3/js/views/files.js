/* ============================================================================
 * View: Files — connected sources & indexed documents.
 * Lists the documents the workspace has ingested, with a human-readable size
 * roll-up and per-file index state. Data comes from /local/list (live) and
 * degrades to clearly-badged sample files when the local agent isn't reachable.
 *
 * View contract (shared by all views):
 *   export async function render(ctx) -> single DOM node
 *   ctx = { h, icon, api, store, c, route, params, navigate, toast }
 * ========================================================================== */

import * as fx from "../core/fixtures.js";
import { timeAgo } from "../core/dom.js";

/** Tabler glyph per file kind — keeps the table scannable. */
const KIND_ICON = {
  markdown: "file-text",
  config: "settings",
  image: "photo",
  data: "table",
  default: "file",
};
const iconForKind = (k) => KIND_ICON[k] || KIND_ICON.default;

/** Bytes → compact human string (1.0 KB / 4.7 KB / 180 KB / 1.2 MB). */
function humanSize(bytes) {
  const n = Number(bytes);
  if (!Number.isFinite(n) || n < 0) return "—";
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v >= 100 || Number.isInteger(v) ? 0 : 1)} ${units[i]}`;
}

/** Live shape may be {files:[...]} or a bare array — normalize defensively. */
function normalize(data) {
  const list = Array.isArray(data) ? data : (data && Array.isArray(data.files) ? data.files : null);
  if (!list) return null;
  return list.map((f) => ({
    name: f.name || (f.path ? String(f.path).split("/").pop() : "untitled"),
    kind: f.kind || "default",
    size: Number(f.size) || 0,
    path: f.path || f.name || "",
    indexed: f.indexed === true,
    updated: f.updated || f.modified || f.mtime || null,
  }));
}

export async function render(ctx) {
  const { h, icon, api, c, navigate, toast } = ctx;

  const pendingToast = () =>
    toast("Folder connection runs through the local agent — pending backend", "info");

  const statHost = h("div.lt3-statrow", c.loading({ lines: 1 }));
  const srcSlot = h("span", c.sourceBadge("pending"));
  const tableHost = h("div", c.loading({ lines: 4 }));

  const root = h("div.lt3-stack-6",
    c.viewHeader({
      eyebrow: "Data",
      title: "Files",
      sub: "Connected sources and the documents Lattice has indexed for retrieval. Everything stays on this machine.",
      actions: [
        h("button.lt3-btn.lt3-btn--ghost", { on: { click: () => navigate("knowledge-graph") } }, icon("chart-dots-3"), "View graph"),
        h("button.lt3-btn.lt3-btn--primary", { on: { click: pendingToast } }, icon("folder-plus"), "Connect folder"),
      ],
    }),
    statHost,
    h("div.lt3-drop",
      h("div.lt3-pillar__icon", icon("cloud-upload")),
      h("div",
        h("div", { style: { "font-weight": "var(--lt3-weight-semi)" } }, "Drag files or connect a folder"),
        h("p.lt3-faint", { style: { "font-size": "var(--lt3-text-sm)", "margin-top": "var(--lt3-space-1)" } },
          "Lattice watches the source, chunks it, embeds it, and links it into the knowledge graph."),
      ),
      h("button.lt3-btn.lt3-btn--ghost", { on: { click: pendingToast } }, icon("folder-plus"), "Choose folder"),
    ),
    c.panel({
      head: h("div.lt3-row", { style: { "justify-content": "space-between", "align-items": "flex-start", width: "100%" } },
        h("div",
          h("div.lt3-eyebrow", "Index"),
          h("h3.lt3-panel__title", "Indexed documents"),
        ),
        srcSlot,
      ),
      children: tableHost,
    }),
  );

  hydrate(ctx, { statHost, srcSlot, tableHost });
  return root;
}

async function hydrate(ctx, slots) {
  const { h, icon, api, c, toast } = ctx;
  const { statHost, srcSlot, tableHost } = slots;

  const res = await api.get("/local/list", fx.FILES);
  const files = normalize(res.data) || normalize(fx.FILES) || [];
  srcSlot.replaceChildren(c.sourceBadge(res.source));

  // ── Stat roll-up ──────────────────────────────────────────────────────────
  const indexedCount = files.filter((f) => f.indexed).length;
  const sourceCount = new Set(
    files.map((f) => (f.path.includes("/") ? f.path.split("/")[0] : "root")),
  ).size;
  const totalBytes = files.reduce((sum, f) => sum + (f.size || 0), 0);
  statHost.replaceChildren(
    c.stat({ label: "Total files", value: c.fmtNum(files.length), icon: "files" }),
    c.stat({ label: "Indexed", value: c.fmtNum(indexedCount), icon: "circle-check" }),
    c.stat({ label: "Sources", value: c.fmtNum(sourceCount), icon: "database" }),
    c.stat({ label: "Total size", value: humanSize(totalBytes), icon: "weight" }),
  );

  // ── Empty state ─────────────────────────────────────────────────────────────
  if (!files.length) {
    tableHost.replaceChildren(c.emptyState({
      icon: "folder-off",
      title: "No documents indexed yet",
      body: "Connect a folder and Lattice will index it for hybrid retrieval.",
      action: h("button.lt3-btn.lt3-btn--primary.lt3-btn--sm",
        { on: { click: () => toast("Folder connection runs through the local agent — pending backend", "info") } },
        icon("folder-plus"), "Connect folder"),
    }));
    return;
  }

  // ── Table ───────────────────────────────────────────────────────────────────
  const columns = [
    {
      key: "name", label: "Name",
      render: (row) => h("div.lt3-row-2",
        h("span.lt3-filerow__icon", icon(iconForKind(row.kind))),
        h("span", { style: { "font-weight": "var(--lt3-weight-medium)" } }, row.name),
      ),
    },
    {
      key: "path", label: "Path", width: "30%",
      render: (row) => h("span.lt3-mono.lt3-faint", row.path || "—"),
    },
    {
      key: "size", label: "Size", width: "92px",
      render: (row) => h("span.lt3-mono", humanSize(row.size)),
    },
    {
      key: "indexed", label: "Indexed", width: "120px",
      render: (row) => row.indexed
        ? c.statePill("indexed")
        : c.statePill("pending"),
    },
    {
      key: "updated", label: "Updated", width: "104px",
      render: (row) => h("span.lt3-faint", { style: { "font-size": "var(--lt3-text-xs)" } },
        row.updated ? timeAgo(row.updated) : "—"),
    },
    {
      key: "_actions", label: "", width: "44px",
      render: (row) => h("button.lt3-iconbtn.lt3-iconbtn--sm", {
        "aria-label": `Actions for ${row.name}`,
        on: { click: () => toast(`Per-file actions for "${row.name}" run through the local agent — pending backend`, "info") },
      }, icon("dots-vertical")),
    },
  ];

  tableHost.replaceChildren(c.table(columns, files));
}
