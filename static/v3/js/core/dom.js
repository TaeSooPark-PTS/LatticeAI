/* ============================================================================
 * Lattice AI v3 — DOM helpers
 * A tiny, dependency-free hyperscript. Builds real DOM nodes (no innerHTML for
 * dynamic content → no injection surface). Ergonomic enough to author views.
 * ========================================================================== */

/**
 * h("div.card#id", { props }, ...children)
 *   - tag selector supports .class (many) and #id
 *   - props: class | className, id, on:{event:fn}, dataset:{k:v},
 *            style:{k:v} or string, html (trusted innerHTML), attrs (any other)
 *   - children: Node | string | number | falsy (skipped) | array (flattened)
 */
export function h(selector, props, ...children) {
  const { tag, id, classes } = parseSelector(selector);
  const el = document.createElement(tag);
  if (id) el.id = id;
  if (classes.length) el.classList.add(...classes);

  if (props && (props.nodeType || typeof props === "string" || Array.isArray(props))) {
    children.unshift(props);
    props = null;
  }

  if (props) {
    for (const [key, val] of Object.entries(props)) {
      if (val == null || val === false) continue;
      if (key === "class" || key === "className") {
        for (const c of String(val).split(/\s+/).filter(Boolean)) el.classList.add(c);
      } else if (key === "on" && typeof val === "object") {
        for (const [ev, fn] of Object.entries(val)) el.addEventListener(ev, fn);
      } else if (key === "dataset" && typeof val === "object") {
        for (const [k, v] of Object.entries(val)) { if (v != null) el.dataset[k] = v; }
      } else if (key === "style" && typeof val === "object") {
        for (const [k, v] of Object.entries(val)) el.style.setProperty(k, v);
      } else if (key === "style") {
        el.setAttribute("style", val);
      } else if (key === "html") {
        el.innerHTML = val;
      } else if (key === "ref" && typeof val === "function") {
        val(el);
      } else if (key in el && key !== "list" && typeof val !== "object") {
        try { el[key] = val; } catch { el.setAttribute(key, val); }
      } else {
        el.setAttribute(key, val === true ? "" : val);
      }
    }
  }

  appendChildren(el, children);
  return el;
}

function parseSelector(sel) {
  if (typeof sel !== "string") return { tag: "div", id: "", classes: [] };
  const idMatch = sel.match(/#([\w-]+)/);
  const id = idMatch ? idMatch[1] : "";
  const classes = (sel.match(/\.([\w-]+)/g) || []).map((c) => c.slice(1));
  const tag = (sel.match(/^([\w-]+)/) || [, "div"])[1];
  return { tag, id, classes };
}

function appendChildren(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false || child === true) continue;
    el.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
}

/** Fragment of many children. */
export function frag(...children) {
  const f = document.createDocumentFragment();
  appendChildren(f, children);
  return f;
}

/** Replace the contents of a node. */
export function render(host, ...children) {
  host.replaceChildren();
  appendChildren(host, children);
  return host;
}

export function clear(host) { host.replaceChildren(); return host; }

export function escapeHtml(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

/** Tabler icon element: icon("home") → <i class="ti ti-home"> */
export function icon(name, extra = "") {
  const i = document.createElement("i");
  i.className = `ti ti-${name}${extra ? " " + extra : ""}`;
  i.setAttribute("aria-hidden", "true");
  return i;
}

/** Compact relative-time formatter. */
export function timeAgo(value) {
  if (!value) return "";
  const then = new Date(value).getTime();
  if (Number.isNaN(then)) return String(value);
  const diff = Math.max(0, Date.now() - then);
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(then).toLocaleDateString();
}

/** Format a number with thousands separators / compact suffix. */
export function fmtNum(n) {
  if (n == null || Number.isNaN(Number(n))) return "—";
  const v = Number(n);
  if (Math.abs(v) >= 1_000_000) return (v / 1_000_000).toFixed(1).replace(/\.0$/, "") + "M";
  if (Math.abs(v) >= 1_000) return (v / 1_000).toFixed(1).replace(/\.0$/, "") + "k";
  return v.toLocaleString();
}

export function debounce(fn, ms = 220) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}
