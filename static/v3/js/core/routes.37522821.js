/* ============================================================================
 * Lattice AI v3 — Information architecture (single source of truth)
 *
 * One declarative table drives the nav rail, command palette, router,
 * breadcrumbs, and lazy view loading. Labels are translated at render time.
 * ========================================================================== */

import { t } from "./i18n.880e1fec.js";

export const MODE_RANK = { basic: 0, advanced: 1, admin: 2 };

export const GROUPS = [
  { id: "brain", labelKey: "group.brain" },
  { id: "ask", labelKey: "group.ask" },
  { id: "capture", labelKey: "group.capture" },
  { id: "act", labelKey: "group.act" },
  { id: "library", labelKey: "group.library" },
  { id: "system", labelKey: "group.system" },
  { id: "admin", labelKey: "group.admin", adminOnly: true },
];

function r(key, labelKey, icon, group, minMode, view, titleKey, descKey, extra = {}) {
  return { key, labelKey, icon, group, minMode, view, titleKey, descKey, ...extra };
}

export const ROUTES = [
  r("home", "route.home.label", "layout-dashboard", "system", "basic", "home", "route.home.title", "route.home.desc"),
  r("account", "route.account.label", "user-circle", "system", "basic", "account", "route.account.title", "route.account.desc"),
  r("chat", "route.chat.label", "message-2", "ask", "basic", "chat", "route.chat.title", "route.chat.desc"),
  r("files", "route.files.label", "folders", "capture", "basic", "files", "route.files.title", "route.files.desc"),

  r("hybrid-search", "route.hybridSearch.label", "arrows-join", "brain", "basic", "hybrid-search", "route.hybridSearch.title", "route.hybridSearch.desc"),
  r("knowledge-graph", "route.knowledgeGraph.label", "chart-dots-3", "brain", "basic", "knowledge-graph", "route.knowledgeGraph.title", "route.knowledgeGraph.desc"),
  r("memory", "route.memory.label", "brain", "brain", "basic", "memory", "route.memory.title", "route.memory.desc"),

  r("models", "route.models.label", "cpu", "library", "basic", "models", "route.models.title", "route.models.desc"),
  r("agents", "route.agents.label", "robot", "act", "advanced", "agents", "route.agents.title", "route.agents.desc"),
  r("runs", "route.runs.label", "progress-check", "act", "advanced", "runs", "route.runs.title", "route.runs.desc"),
  r("workflows", "route.workflows.label", "sitemap", "act", "advanced", "workflows", "route.workflows.title", "route.workflows.desc"),

  r("skills", "route.skills.label", "puzzle", "library", "advanced", "skills", "route.skills.title", "route.skills.desc"),
  r("hooks", "route.hooks.label", "webhook", "act", "advanced", "hooks", "route.hooks.title", "route.hooks.desc"),
  r("mcp", "route.mcp.label", "plug-connected", "library", "advanced", "mcp", "route.mcp.title", "route.mcp.desc"),

  r("workspace-admin", "route.workspaceAdmin.label", "building-community", "system", "basic", "workspace-admin", "route.workspaceAdmin.title", "route.workspaceAdmin.desc"),
  r("snapshots", "route.snapshots.label", "history", "system", "basic", "snapshots", "route.snapshots.title", "route.snapshots.desc"),
  r("activity", "route.activity.label", "activity", "system", "basic", "activity", "route.activity.title", "route.activity.desc"),
  r("network", "route.network.label", "network", "system", "advanced", "network", "route.network.title", "route.network.desc"),
  r("settings", "route.settings.label", "settings", "system", "basic", "settings", "route.settings.title", "route.settings.desc"),

  r("pipeline", "route.pipeline.label", "git-branch", "capture", "advanced", "pipeline", "route.pipeline.title", "route.pipeline.desc", { hidden: true }),
  r("planning", "route.planning.label", "target-arrow", "act", "advanced", "planning", "route.planning.title", "route.planning.desc", { hidden: true }),
  r("my-computer", "route.myComputer.label", "device-desktop-analytics", "system", "advanced", "my-computer", "route.myComputer.title", "route.myComputer.desc", { hidden: true }),
  r("marketplace", "route.marketplace.label", "building-store", "library", "advanced", "marketplace", "route.marketplace.title", "route.marketplace.desc", { hidden: true }),
  r("tools", "route.tools.label", "tools", "act", "advanced", "tools", "route.tools.title", "route.tools.desc", { hidden: true }),

  r("admin/users", "route.adminUsers.label", "users", "admin", "admin", "admin-users", "route.adminUsers.title", "route.adminUsers.desc", { admin: true }),
  r("admin/permissions", "route.adminPermissions.label", "key", "admin", "admin", "admin-permissions", "route.adminPermissions.title", "route.adminPermissions.desc", { admin: true }),
  r("admin/audit", "route.adminAudit.label", "report-search", "admin", "admin", "admin-audit", "route.adminAudit.title", "route.adminAudit.desc", { admin: true }),
  r("admin/security", "route.adminSecurity.label", "shield-check", "admin", "admin", "admin-security", "route.adminSecurity.title", "route.adminSecurity.desc", { admin: true }),
  r("admin/policies", "route.adminPolicies.label", "file-certificate", "admin", "admin", "admin-policies", "route.adminPolicies.title", "route.adminPolicies.desc", { admin: true }),
  r("admin/private-vpc", "route.adminVpc.label", "cloud-lock", "admin", "admin", "admin-private-vpc", "route.adminVpc.title", "route.adminVpc.desc", { admin: true }),
];

export const ROUTE_BY_KEY = Object.fromEntries(ROUTES.map((route) => [route.key, route]));

export function groupLabel(group) {
  return t(group.labelKey || group.id);
}

export function localizeRoute(route) {
  if (!route) return route;
  return {
    ...route,
    label: t(route.labelKey),
    title: t(route.titleKey || route.labelKey),
    desc: t(route.descKey || ""),
  };
}

export function visibleRoutes(mode) {
  const rank = MODE_RANK[mode] ?? 0;
  return ROUTES.filter((route) => {
    if (route.hidden) return false;
    if (route.admin) return mode === "admin";
    return (MODE_RANK[route.minMode] ?? 0) <= rank;
  }).map(localizeRoute);
}

const cache = new Map();
function assetUrl(key, fallback) {
  const manifest = window.__LT_ASSET_MANIFEST__;
  return (manifest && manifest.assets && manifest.assets[key]) || fallback;
}

export async function loadView(view) {
  if (cache.has(view)) return cache.get(view);
  const mod = await import(assetUrl(`static/v3/js/views/${view}.js`, `../views/${view}.js`));
  cache.set(view, mod);
  return mod;
}
