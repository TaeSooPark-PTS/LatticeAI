// Lattice AI v2.0 — shared helpers for the Agentic Workspace Platform pages.
export const NAV = [
  { href: "/workspace", label: "Dashboard" },
  { href: "/plugins/sdk", label: "Plugins" },
  { href: "/workflows", label: "Workflows" },
  { href: "/agents", label: "Agents" },
  { href: "/activity", label: "Activity" },
  { href: "/chat", label: "Chat" },
];

export function mountHeader(active) {
  const links = NAV.map(
    (n) => `<a href="${n.href}" class="${n.href === active ? "active" : ""}">${n.label}</a>`
  ).join("");
  document.body.insertAdjacentHTML(
    "afterbegin",
    `<header class="app"><div class="brand">Lattice AI<small>v2.0 Platform</small></div><nav>${links}</nav></header>`
  );
}

export async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    ...opts,
  });
  if (res.status === 401) {
    location.href = "/account";
    throw new Error("unauthorized");
  }
  const text = await res.text();
  let body;
  try { body = text ? JSON.parse(text) : {}; } catch { body = { raw: text }; }
  if (!res.ok) {
    const detail = body && body.detail ? (typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail)) : res.statusText;
    throw new Error(detail);
  }
  return body;
}

export function el(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}

export function escapeHtml(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

export function toast(msg) {
  const node = el(`<div class="toast">${escapeHtml(msg)}</div>`);
  document.body.appendChild(node);
  setTimeout(() => node.remove(), 4000);
}

export function badge(status) {
  const cls = { ok: "ok", ready: "ok", valid: "ok", retried_ok: "ok",
    partial: "warn", retry: "warn", skipped: "warn", available: "warn",
    failed: "err", error: "err", blocked: "err" }[status] || "";
  return `<span class="badge ${cls}">${escapeHtml(status || "?")}</span>`;
}
