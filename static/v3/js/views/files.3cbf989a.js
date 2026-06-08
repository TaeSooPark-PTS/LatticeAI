/* ============================================================================
 * View: Files — connected sources & indexed documents.
 * Lists the sources the workspace has indexed, with a human-readable status
 * roll-up. Data comes from /workspace/indexing (live); when indexing is
 * unavailable, the table renders an empty unavailable state.
 *
 * View contract (shared by all views):
 *   export async function render(ctx) -> single DOM node
 *   ctx = { h, icon, api, store, c, route, params, navigate, toast }
 * ========================================================================== */

import { timeAgo } from "../core/dom.a2773eb0.js";

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
  if (bytes === null || bytes === undefined || bytes === "") return "—";
  const n = Number(bytes);
  if (!Number.isFinite(n) || n < 0) return "—";
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v >= 100 || Number.isInteger(v) ? 0 : 1)} ${units[i]}`;
}

/** Live shape is {sources:[...]}; legacy {files:[...]} payloads normalize too. */
function normalize(data) {
  if (data && Array.isArray(data.sources)) {
    return data.sources.map((source) => ({
      name: source.label || source.id || "local source",
      kind: "default",
      size: null,
      path: source.root_path || source.id || "",
      indexed: Number(source.success_count || 0) > 0,
      updated: source.last_run_at || source.updated_at || null,
      count: Number(source.success_count || 0),
      status: source.status || (source.watch_active ? "watching" : "idle"),
    }));
  }
  const list = Array.isArray(data) ? data : (data && Array.isArray(data.files) ? data.files : null);
  if (!list) return null;
  return list.map((f) => ({
    name: f.name || (f.path ? String(f.path).split("/").pop() : "untitled"),
    kind: f.kind || "default",
    size: Number(f.size) || 0,
    path: f.path || f.name || "",
    indexed: f.indexed === true,
    updated: f.updated || f.modified || f.mtime || null,
    count: Number(f.count || 0),
    status: f.status || null,
  }));
}

/** Document types the backend accepts (latticeai/services/upload_service.py). */
const UPLOAD_ACCEPT = ".pdf,.docx,.xlsx,.pptx,.txt,.md,.csv";

export async function render(ctx) {
  const { h, icon, api, c, navigate, toast } = ctx;

  // Connecting/watching a *folder* needs the desktop local-agent connector,
  // which is not enabled in this build. Say so plainly. Manual document upload
  // below works without it.
  const unavailableToast = () =>
    toast("Connecting a folder requires the Lattice desktop local agent — not available in this build.", "warn");

  const statHost = h("div.lt3-statrow", c.loading({ lines: 1 }));
  const srcSlot = h("span", c.sourceBadge("pending"));
  const tableHost = h("div", c.loading({ lines: 4 }));

  // ── Manual upload (works in this build; no desktop agent required) ─────────
  let busy = false;
  const fileInput = h("input", {
    type: "file", multiple: true, accept: UPLOAD_ACCEPT,
    style: { display: "none" }, "aria-hidden": "true",
    on: { change: (e) => uploadFiles(e.target.files) },
  });
  const pickFiles = () => { if (!busy) fileInput.click(); };
  const slots = { statHost, srcSlot, tableHost, pickFiles };

  async function uploadFiles(fileList) {
    const files = Array.from(fileList || []);
    if (!files.length || busy) return;
    busy = true;
    let ok = 0;
    for (const file of files) {
      toast(`Uploading “${file.name}”…`, "info");
      const res = await api.uploadDocument(file);
      if (res.ok && res.data && !res.data.detail && !res.data.error) {
        ok++;
      } else {
        const detail = (res.data && (res.data.detail || res.data.error)) || "the backend is unavailable";
        toast(`Could not ingest “${file.name}” — ${detail}.`, "warn");
      }
    }
    fileInput.value = "";
    busy = false;
    if (ok) {
      toast(`Indexed ${ok} document${ok === 1 ? "" : "s"} into the knowledge graph — now searchable in Chat and Hybrid Search.`, "ok");
    }
    hydrate(ctx, slots);
  }

  const dropZone = h("div.lt3-drop", {
    on: {
      dragover: (e) => { e.preventDefault(); dropZone.classList.add("is-dragover"); },
      dragleave: () => dropZone.classList.remove("is-dragover"),
      drop: (e) => { e.preventDefault(); dropZone.classList.remove("is-dragover"); uploadFiles(e.dataTransfer && e.dataTransfer.files); },
    },
  },
    fileInput,
    h("div.lt3-pillar__icon", icon("cloud-upload")),
    h("div",
      h("div", { style: { "font-weight": "var(--lt3-weight-semi)" } }, "Drag documents here, or upload manually"),
      h("p.lt3-faint", { style: { "font-size": "var(--lt3-text-sm)", "margin-top": "var(--lt3-space-1)" } },
        "Lattice parses each file, chunks it, embeds it, and links it into the knowledge graph. PDF · DOCX · XLSX · PPTX · TXT · MD · CSV, up to 10 MB each."),
    ),
    h("div.lt3-drop__meta",
      c.pill("Manual upload available", "ok", { dot: true }),
      c.pill("Folder connection needs local agent", "warn", { dot: true }),
      c.pill("Search + Chat ready after indexing", "info", { dot: true }),
    ),
    h("button.lt3-btn.lt3-btn--primary", { type: "button", on: { click: pickFiles } }, icon("upload"), "Upload files"),
  );

  const root = h("div.lt3-stack-6",
    c.viewHeader({
      eyebrow: "Data",
      title: "Files",
      sub: "Connected sources and the documents Lattice has indexed for retrieval. Everything stays on this machine.",
      actions: [
        h("button.lt3-btn.lt3-btn--ghost", { on: { click: () => navigate("knowledge-graph") } }, icon("chart-dots-3"), "View graph"),
        h("button.lt3-btn.lt3-btn--primary", { on: { click: pickFiles } }, icon("upload"), "Upload files"),
        h("button.lt3-btn.lt3-btn--ghost", { title: "Requires the desktop local agent (not in this build)", on: { click: unavailableToast } }, icon("folder-plus"), "Connect folder"),
      ],
    }),
    statHost,
    dropZone,
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

  hydrate(ctx, slots);
  return root;
}

async function hydrate(ctx, slots) {
  const { h, icon, api, c, toast } = ctx;
  const { statHost, srcSlot, tableHost, pickFiles } = slots;

  const probe = await api.get("/workspace/indexing", { sources: [], totals: {} });
  const liveFiles = probe.ok && probe.data ? normalize(probe.data) : null;
  const source = probe.source || (liveFiles ? "live" : "unavailable");
  const files = liveFiles || [];
  srcSlot.replaceChildren(c.sourceBadge(source));

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
      body: "Upload a document and Lattice will parse, embed, and link it into the knowledge graph for hybrid retrieval.",
      action: h("button.lt3-btn.lt3-btn--primary.lt3-btn--sm",
        { on: { click: () => (pickFiles ? pickFiles() : null) } },
        icon("upload"), "Upload files"),
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
      key: "count", label: "Indexed", width: "92px",
      render: (row) => h("span.lt3-mono", row.count ? c.fmtNum(row.count) : humanSize(row.size)),
    },
    {
      key: "status", label: "Status", width: "120px",
      render: (row) => c.statePill(row.indexed ? "indexed" : (row.status || "pending")),
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
        title: "Requires the desktop local agent (not in this build)",
        on: { click: () => toast(`Per-file actions require the Lattice desktop local agent — not available in this build.`, "warn") },
      }, icon("dots-vertical")),
    },
  ];

  tableHost.replaceChildren(c.table(columns, files));
}
