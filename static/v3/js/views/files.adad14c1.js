/* ============================================================================
 * View: Files — uploaded documents, connected folders & folder watch.
 * The headline table lists the documents Lattice has actually ingested
 * (/knowledge-graph/documents, live) with a per-doc index-state pill. A manual
 * upload drop zone ingests files on-device, and Connect Folder indexes a local
 * directory (+ watches it for changes) over the on-device runtime. When a
 * surface is unavailable its panel renders an honest empty/unavailable state —
 * no counts or statuses are fabricated.
 *
 * View contract (shared by all views):
 *   export async function render(ctx) -> single DOM node
 *   ctx = { h, icon, api, store, c, route, params, navigate, toast }
 * ========================================================================== */

import { timeAgo } from "../core/dom.a2773eb0.js";

/** Tabler glyph per uploaded-document extension. */
const EXT_ICON = {
  pdf: "file-type-pdf", docx: "file-type-docx", doc: "file-text",
  xlsx: "file-spreadsheet", xls: "file-spreadsheet", csv: "table",
  pptx: "presentation", ppt: "presentation",
  md: "file-text", txt: "file-text", json: "file-code",
  png: "photo", jpg: "photo", jpeg: "photo", gif: "photo",
};
const iconForExt = (ext) => EXT_ICON[String(ext || "").replace(/^\./, "").toLowerCase()] || "file";

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

/** Document types the backend accepts (latticeai/services/upload_service.py). */
const UPLOAD_ACCEPT = ".pdf,.docx,.xlsx,.pptx,.txt,.md,.csv";

export async function render(ctx) {
  const { h, icon, api, c, navigate, toast } = ctx;

  const statHost = h("div.lt3-statrow", c.loading({ lines: 1 }));
  const srcSlot = h("span", c.sourceBadge("pending"));
  const tableHost = h("div", c.loading({ lines: 4 }));
  const foldersSrc = h("span", c.sourceBadge("pending"));
  const foldersHost = h("div", c.loading({ lines: 3 }));

  // ── Manual upload (works in this build; no desktop agent required) ─────────
  let busy = false;
  const fileInput = h("input", {
    type: "file", multiple: true, accept: UPLOAD_ACCEPT,
    style: { display: "none" }, "aria-hidden": "true",
    on: { change: (e) => uploadFiles(e.target.files) },
  });
  const pickFiles = () => { if (!busy) fileInput.click(); };
  const slots = { statHost, srcSlot, tableHost, foldersSrc, foldersHost, pickFiles, connectFolder };

  // ── Connect Folder — index a local directory on-device and watch it ────────
  // Available now via the on-device runtime (one call does
  // request → self-approve → index + watch). No desktop agent required.
  async function connectFolder() {
    if (busy) return;
    const path = window.prompt("Connect a local folder to index (absolute path)", "~/Documents");
    if (!path || !String(path).trim()) return;
    const target = String(path).trim();
    busy = true;
    toast(`Connecting “${target}” — indexing on-device…`, "info");
    const res = await api.connectFolder(target, { watch: true });
    busy = false;
    if (res.ok) {
      toast(`Connected and indexing ${target} — now watched for changes.`, "ok");
      hydrate(ctx, slots);
    } else {
      toast(res.error || "Could not connect the folder.", "warn");
    }
  }

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
      c.pill("Connect a local folder — indexed & watched on-device", "info", { dot: true }),
      c.pill("Search + Chat ready after indexing", "info", { dot: true }),
    ),
    h("div.lt3-row-2",
      h("button.lt3-btn.lt3-btn--primary", { type: "button", on: { click: pickFiles } }, icon("upload"), "Upload files"),
      h("button.lt3-btn.lt3-btn--ghost", { type: "button", on: { click: connectFolder } }, icon("folder-plus"), "Connect folder"),
    ),
  );

  const root = h("div.lt3-stack-6",
    c.viewHeader({
      eyebrow: "Data",
      title: "Files",
      sub: "Connected sources and the documents Lattice has indexed for retrieval. Everything stays on this machine.",
      actions: [
        h("button.lt3-btn.lt3-btn--ghost", { on: { click: () => navigate("knowledge-graph") } }, icon("chart-dots-3"), "View graph"),
        h("button.lt3-btn.lt3-btn--primary", { on: { click: pickFiles } }, icon("upload"), "Upload files"),
        h("button.lt3-btn.lt3-btn--ghost", { title: "Index a local folder on-device and watch it for changes", on: { click: connectFolder } }, icon("folder-plus"), "Connect folder"),
      ],
    }),
    statHost,
    dropZone,
    c.panel({
      head: h("div.lt3-row", { style: { "justify-content": "space-between", "align-items": "flex-start", width: "100%" } },
        h("div",
          h("div.lt3-eyebrow", "Index"),
          h("h3.lt3-panel__title", "Uploaded documents"),
          h("p.lt3-panel__sub", "Every file Lattice has parsed, chunked, embedded and linked into the knowledge graph."),
        ),
        srcSlot,
      ),
      children: tableHost,
    }),
    c.panel({
      head: h("div.lt3-row", { style: { "justify-content": "space-between", "align-items": "flex-start", width: "100%" } },
        h("div",
          h("div.lt3-eyebrow", "Local sources"),
          h("h3.lt3-panel__title", "Connected folders & folder watch"),
          h("p.lt3-panel__sub", "Local directories Lattice indexes on-device and re-indexes when their files change."),
        ),
        foldersSrc,
      ),
      children: foldersHost,
    }),
  );

  hydrate(ctx, slots);
  return root;
}

async function hydrate(ctx, slots) {
  const { statHost, srcSlot, tableHost, foldersSrc, foldersHost, pickFiles } = slots;

  // Fetch the documents (headline table) and connected local sources in parallel.
  const [docsRes, sourcesRes] = await Promise.all([
    ctx.api.documents(200),
    ctx.api.localSources(),
  ]);

  hydrateDocuments(ctx, { statHost, srcSlot, tableHost, pickFiles }, docsRes);
  hydrateFolders(ctx, { foldersSrc, foldersHost, slots }, sourcesRes);
}

/** Headline "Uploaded documents" table + stat roll-up. Data: api.documents(). */
function hydrateDocuments(ctx, { statHost, srcSlot, tableHost, pickFiles }, docsRes) {
  const { h, icon, c, toast } = ctx;
  const docs = Array.isArray(docsRes.data) ? docsRes.data : [];
  const source = docsRes.source || (docsRes.ok ? "live" : "unavailable");
  srcSlot.replaceChildren(c.sourceBadge(source));

  // ── Stat roll-up (driven by the real documents list) ──────────────────────
  const indexedCount = docs.filter((d) => d.indexed === true || d.ingest_state === "indexed").length;
  const sourceCount = new Set(
    docs.map((d) => (d.uploader ? `u:${d.uploader}` : `e:${String(d.ext || "").toLowerCase()}`)),
  ).size;
  const totalBytes = docs.reduce((sum, d) => sum + (Number(d.bytes) || 0), 0);
  statHost.replaceChildren(
    c.stat({ label: "Total files", value: c.fmtNum(docs.length), icon: "files" }),
    c.stat({ label: "Indexed", value: c.fmtNum(indexedCount), icon: "circle-check" }),
    c.stat({ label: "Sources", value: c.fmtNum(sourceCount), icon: "database" }),
    c.stat({ label: "Total size", value: humanSize(totalBytes), icon: "weight" }),
  );

  // ── Empty / unavailable state ─────────────────────────────────────────────
  if (!docs.length) {
    if (!docsRes.ok) {
      tableHost.replaceChildren(c.errorState(
        docsRes.error || "The document index is unavailable. Start the backend with the knowledge graph enabled.",
      ));
      return;
    }
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

  // ── Table ─────────────────────────────────────────────────────────────────
  const columns = [
    {
      key: "filename", label: "Name",
      render: (row) => h("div.lt3-row-2",
        h("span.lt3-filerow__icon", icon(iconForExt(row.ext))),
        h("span", { style: { "font-weight": "var(--lt3-weight-medium)" } }, row.filename || "untitled"),
      ),
    },
    {
      key: "uploader", label: "Uploaded by", width: "26%",
      render: (row) => h("span.lt3-mono.lt3-faint", row.uploader || "—"),
    },
    {
      key: "chars", label: "Size", width: "100px",
      render: (row) => h("span.lt3-mono",
        Number(row.chars) > 0 ? `${c.fmtNum(row.chars)} chars` : humanSize(row.bytes)),
    },
    {
      key: "chunks", label: "Chunks", width: "84px",
      render: (row) => h("span.lt3-mono", Number(row.chunks) > 0 ? c.fmtNum(row.chunks) : "—"),
    },
    {
      key: "ingest_state", label: "Index", width: "120px",
      // "indexed" → green, "ingested" → warn (via components STATE_VARIANT).
      render: (row) => c.statePill(row.ingest_state || (row.indexed ? "indexed" : "ingested")),
    },
    {
      key: "updated_at", label: "Updated", width: "104px",
      render: (row) => h("span.lt3-faint", { style: { "font-size": "var(--lt3-text-xs)" } },
        (row.updated_at || row.created_at) ? timeAgo(row.updated_at || row.created_at) : "—"),
    },
    {
      key: "_actions", label: "", width: "44px",
      // Per-file management is limited — say so honestly rather than implying delete/re-index.
      render: (row) => h("button.lt3-iconbtn.lt3-iconbtn--sm", {
        "aria-label": `Document info for ${row.filename || "file"}`,
        title: "Per-document management isn't available yet",
        on: { click: () => toast("Per-document management (delete / re-index) isn't available yet — re-upload to refresh a file.", "info") },
      }, icon("dots-vertical")),
    },
  ];

  tableHost.replaceChildren(c.table(columns, docs));
}

/** Connected local folders + folder-watch state. Data: api.localSources(). */
function hydrateFolders(ctx, { foldersSrc, foldersHost, slots }, res) {
  const { h, icon, c, toast } = ctx;
  const data = res.data || {};
  const sources = Array.isArray(data.sources) ? data.sources : [];
  const watch = data.watch || {};
  const source = res.source || (res.ok ? "live" : "unavailable");
  foldersSrc.replaceChildren(c.sourceBadge(source));

  const kids = [];

  // Honest note when filesystem watching can't run (watchdog dependency missing).
  if (watch.available === false) {
    kids.push(c.banner(
      watch.error
        ? `Folder watch is off: ${watch.error}`
        : "Folder watch needs the watchdog dependency — connected folders index once but won't re-index automatically until it's installed.",
      "warn",
      "alert-triangle",
    ));
  }

  if (!sources.length) {
    if (!res.ok) {
      kids.push(c.errorState("Local sources are unavailable — the on-device runtime isn't reachable."));
    } else {
      kids.push(c.emptyState({
        icon: "folder-plus",
        title: "No folders connected",
        body: "Connect a local folder — Lattice indexes it on-device and watches it for changes.",
        action: h("button.lt3-btn.lt3-btn--primary.lt3-btn--sm",
          { on: { click: () => (slots.connectFolder ? slots.connectFolder() : null) } },
          icon("folder-plus"), "Connect folder"),
      }));
    }
    foldersHost.replaceChildren(...kids);
    return;
  }

  // ── Connected-folders table ───────────────────────────────────────────────
  async function stopWatching(id) {
    toast("Stopping folder watch…", "info");
    const stop = await ctx.api.localWatchStop(id);
    if (stop.ok && stop.data && !stop.data.detail && !stop.data.error) {
      toast("Stopped watching that folder.", "ok");
    } else {
      const detail = (stop.data && (stop.data.detail || stop.data.error)) || "the runtime is unavailable";
      toast(`Could not stop watching — ${detail}.`, "warn");
    }
    hydrate(ctx, slots);
  }

  const columns = [
    {
      key: "label", label: "Folder",
      render: (row) => h("div",
        h("div.lt3-row-2",
          h("span.lt3-filerow__icon", icon("folder")),
          h("span", { style: { "font-weight": "var(--lt3-weight-medium)" } }, row.label || "Local folder"),
        ),
        h("div.lt3-mono.lt3-faint", { style: { "font-size": "var(--lt3-text-xs)", "margin-top": "var(--lt3-space-1)" } },
          row.root_path || row.id || "—"),
      ),
    },
    {
      key: "success_count", label: "Indexed", width: "92px",
      render: (row) => h("span.lt3-mono", c.fmtNum(Number(row.success_count) || 0)),
    },
    {
      key: "watch_active", label: "Watch", width: "120px",
      render: (row) => c.statePill(row.watch_active ? "watching" : "idle"),
    },
    {
      key: "watch_status", label: "Last activity", width: "150px",
      render: (row) => {
        const ws = row.watch_status || {};
        if (ws.last_error) {
          return h("span", { style: { color: "var(--danger)", "font-size": "var(--lt3-text-xs)" } }, ws.last_error);
        }
        const at = ws.last_event_at || ws.last_indexed_at;
        const label = ws.last_event_at ? "event" : (ws.last_indexed_at ? "indexed" : "");
        return h("span.lt3-faint", { style: { "font-size": "var(--lt3-text-xs)" } },
          at ? `${timeAgo(at)}${label ? ` · ${label}` : ""}` : "—");
      },
    },
    {
      key: "_stop", label: "", width: "120px",
      render: (row) => row.watch_active
        ? h("button.lt3-btn.lt3-btn--subtle.lt3-btn--sm", {
            on: { click: () => stopWatching(row.id) },
          }, icon("player-stop"), "Stop watching")
        : h("span.lt3-faint", { style: { "font-size": "var(--lt3-text-xs)" } }, "Not watching"),
    },
  ];

  kids.push(c.table(columns, sources));
  foldersHost.replaceChildren(...kids);
}
