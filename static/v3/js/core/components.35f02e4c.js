/* ============================================================================
 * Lattice AI v3 — Component factories
 * Build the shared vocabulary (cards, panels, stats, tables, states, the
 * retrieval-lattice pillars …) on top of dom.js + the component CSS. Views
 * compose these to stay visually consistent.
 * ========================================================================== */

import { h, icon, fmtNum } from "./dom.a2773eb0.js";

/* ── View + section headers ─────────────────────────────────────────────── */
export function viewHeader({ eyebrow, title, sub, actions } = {}) {
  return h("header.lt3-vhead",
    h("div",
      eyebrow && h("div.lt3-eyebrow", eyebrow),
      h("h1.lt3-vhead__title", title),
      sub && h("p.lt3-vhead__sub", sub),
    ),
    actions && actions.length && h("div.lt3-vhead__actions", actions),
  );
}

export function sectionHead(title, ...actions) {
  return h("div.lt3-section__head",
    h("h2.lt3-section__title", title),
    actions.length && h("div.lt3-row-2", actions),
  );
}

/* ── Panel / card ───────────────────────────────────────────────────────── */
export function panel({ title, sub, actions, head, children, eyebrow, className } = {}) {
  return h(`section.lt3-panel${className ? "." + className : ""}`,
    (title || head || actions) && h("div.lt3-panel__head",
      head || h("div",
        eyebrow && h("div.lt3-eyebrow", eyebrow),
        title && h("h3.lt3-panel__title", title),
        sub && h("p.lt3-panel__sub", sub),
      ),
      actions && h("div.lt3-row-2", actions),
    ),
    children,
  );
}

export function card(children, opts = {}) {
  const cls = ["lt3-card"];
  if (opts.interactive) cls.push("lt3-card--interactive");
  if (opts.flat) cls.push("lt3-card--flat");
  if (opts.ghost) cls.push("lt3-card--ghost");
  return h(`div.${cls.join(".")}`, opts.attrs || {}, children);
}

/* ── Stat tile ──────────────────────────────────────────────────────────── */
export function stat({ label, value, icon: ic, delta, deltaDir }) {
  return h("div.lt3-stat",
    h("div.lt3-stat__label", ic && icon(ic), label),
    h("div.lt3-stat__value", value == null ? "—" : value),
    delta && h(`div.lt3-stat__delta${deltaDir ? ".lt3-stat__delta--" + deltaDir : ""}`, delta),
  );
}

/* ── Pills / badges ─────────────────────────────────────────────────────── */
export function pill(text, variant = "", { dot } = {}) {
  const cls = ["lt3-pill"];
  if (variant) cls.push("lt3-pill--" + variant);
  if (dot) cls.push("lt3-pill--dot");
  return h(`span.${cls.join(".")}`, text);
}

const STATE_VARIANT = {
  ready: "ok", active: "ok", indexed: "ok", loaded: "ok", ok: "ok", available: "info",
  idle: "", standby: "", pending: "warn", indexing: "warn", building: "warn",
  failed: "err", error: "err", disabled: "err", not_configured: "",
  // v3.4.0 platform-completion states (Files / Folder Watch / Local Agent /
  // Agent runs / Hook dispatch). Keep these honest: amber for in-progress,
  // green for healthy/active, red for blocked/failed, neutral for inert.
  ingested: "warn", ingesting: "warn", watching: "ok", watched: "ok",
  connected: "ok", online: "ok", offline: "err", synced: "ok",
  queued: "warn", running: "warn", retrying: "warn", retried_ok: "ok",
  rejected: "err", cancelled: "", stopped: "", blocked: "err",
  advisory: "warn", skipped: "", complete: "ok", partial: "warn",
};
export function statePill(state) {
  return pill(String(state || "unknown"), STATE_VARIANT[String(state).toLowerCase()] ?? "", { dot: true });
}

/** Provenance badge — makes live vs unavailable data explicit. */
export function sourceBadge(source) {
  if (source === "live") return h("span.lt3-source.lt3-source--live", icon("circle-filled"), "Live");
  if (source === "unavailable") return h("span.lt3-source.lt3-source--unavailable", icon("alert-circle"), "Unavailable");
  return h("span.lt3-source.lt3-source--pending", "—");
}

/* ── States ─────────────────────────────────────────────────────────────── */
export function emptyState({ icon: ic = "inbox", title, body, action } = {}) {
  return h("div.lt3-empty",
    h("div.lt3-empty__icon", icon(ic)),
    title && h("div.lt3-empty__title", title),
    body && h("div.lt3-empty__body", body),
    action,
  );
}

export function loading({ lines = 3, block = false } = {}) {
  const kids = [];
  if (block) kids.push(h("div.lt3-skel.lt3-skel--block"));
  for (let i = 0; i < lines; i++) {
    kids.push(h("div.lt3-skel.lt3-skel--line", { style: { width: 100 - i * 14 + "%" } }));
  }
  return h("div", { "aria-busy": "true", "aria-label": "Loading" }, kids);
}

export function errorState(message, onRetry) {
  return h("div.lt3-banner.lt3-banner--err",
    icon("alert-triangle"),
    h("div", h("div", { style: { fontWeight: 600 } }, "Couldn't load"), h("div.lt3-faint", message || "Request failed")),
    onRetry && h("button.lt3-btn.lt3-btn--subtle.lt3-btn--sm", { style: { "margin-left": "auto" }, on: { click: onRetry } }, icon("refresh"), "Retry"),
  );
}

export function banner(text, variant = "info", ic = "info-circle") {
  return h(`div.lt3-banner.lt3-banner--${variant}`, icon(ic), h("div", text));
}

/* ── Table ──────────────────────────────────────────────────────────────── */
export function table(columns, rows, { empty } = {}) {
  if (!rows || !rows.length) {
    return empty || emptyState({ title: "Nothing here yet", body: "Data will appear once connected." });
  }
  return h("div.lt3-table--clip", { style: { overflow: "auto" } },
    h("table.lt3-table",
      h("thead", h("tr", columns.map((c) => h("th", { style: c.width ? { width: c.width } : {} }, c.label)))),
      h("tbody", rows.map((row) => h("tr", columns.map((c) => h("td", c.render ? c.render(row) : row[c.key]))))),
    ),
  );
}

/* ── Tabs / segmented ───────────────────────────────────────────────────── */
export function tabs(items, active, onChange) {
  return h("div.lt3-tabs", { role: "tablist" },
    items.map((it) => h("button.lt3-tab", {
      role: "tab", type: "button",
      dataset: { active: String(it.key === active) },
      "aria-selected": String(it.key === active),
      on: { click: () => onChange(it.key) },
    }, it.label)),
  );
}

export function segmented(items, active, onChange) {
  return h("div.lt3-seg", { role: "tablist" },
    items.map((it) => h("button", {
      type: "button", role: "tab",
      dataset: { active: String(it.key === active) },
      "aria-selected": String(it.key === active),
      on: { click: () => onChange(it.key) },
    }, it.label)),
  );
}

/* ── Meter ──────────────────────────────────────────────────────────────── */
export function meter(value, variant = "") {
  const pct = Math.max(0, Math.min(1, Number(value) || 0)) * 100;
  return h("div.lt3-meter",
    h(`div.lt3-meter__fill${variant ? ".lt3-meter__fill--" + variant : ""}`, { style: { width: pct + "%" } }),
  );
}

/* ── Retrieval lattice (signature) ──────────────────────────────────────── */
const PILLAR_DEFS = [
  { key: "knowledge_graph", kind: "graph", name: "Knowledge Graph", desc: "Entities & relations", icon: "chart-dots-3", unit: "entities", read: (p) => p?.entities },
  { key: "vector_index", kind: "vector", name: "Vector Index", desc: "Local embedding vectors", icon: "grid-dots", unit: "vectors", read: (p) => p?.vectors },
  { key: "hybrid", kind: "hybrid", name: "Hybrid Search", desc: "Fused graph + vector", icon: "arrows-join", unit: "fusion", read: (p) => p?.strategy },
];

export function pillars(indexStatus) {
  const pipes = indexStatus?.pipelines || {};
  if (!Object.keys(pipes).length) {
    return emptyState({
      icon: "database-off",
      title: "Retrieval status unavailable",
      body: "Start the backend with Knowledge Graph enabled to see live index state.",
    });
  }
  return h("div.lt3-pillars",
    PILLAR_DEFS.map((def) => {
      const p = pipes[def.key] || {};
      const raw = def.read(p);
      const num = typeof raw === "number" ? fmtNum(raw) : (raw || "ready");
      return h(`article.lt3-pillar.lt3-pillar--${def.kind}`,
        h("div.lt3-pillar__icon", icon(def.icon)),
        h("div.lt3-row", { style: { "justify-content": "space-between" } },
          h("div",
            h("div.lt3-pillar__name", def.name),
            h("div.lt3-pillar__desc", def.desc),
          ),
          statePill(p.state || "ready"),
        ),
        h("div.lt3-pillar__stat",
          h("span.lt3-pillar__num", num),
          h("span.lt3-pillar__unit", def.unit),
        ),
      );
    }),
  );
}

/** Compact 3-dot index chip for the topbar. */
export function indexChip(indexStatus) {
  const pipes = indexStatus?.pipelines || {};
  const dot = (kind, key) => h("span.lt3-idxchip__dot", {
    dataset: { kind, on: String((pipes[key]?.state || "ready") === "ready") },
    title: `${kind}: ${pipes[key]?.state || "—"}`,
  });
  return h("div.lt3-idxchip", { title: "Retrieval index status" },
    h("span.lt3-idxchip__dots", dot("graph", "knowledge_graph"), dot("vector", "vector_index"), dot("hybrid", "hybrid")),
    h("span", "Index"),
  );
}

/* ── Toast ──────────────────────────────────────────────────────────────── */
export function toast(message, variant = "info") {
  let host = document.querySelector(".lt3-toasts");
  if (!host) { host = h("div.lt3-toasts"); document.body.append(host); }
  const ic = variant === "ok" ? "circle-check" : variant === "err" ? "alert-circle" : "info-circle";
  const node = h(`div.lt3-toast.lt3-toast--${variant}`, icon(ic), h("div", message));
  host.append(node);
  setTimeout(() => { node.style.opacity = "0"; node.style.transition = "opacity .3s"; setTimeout(() => node.remove(), 300); }, 3200);
}

export { icon, fmtNum };
