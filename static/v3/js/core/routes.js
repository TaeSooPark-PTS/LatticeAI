/* ============================================================================
 * Lattice AI v3 — Information architecture (single source of truth)
 *
 * One declarative table drives the nav rail, the command palette, the router,
 * breadcrumbs, and lazy view loading. Mode gating (Basic < Advanced < Admin)
 * and the Admin section live here so the whole shell stays consistent.
 * ========================================================================== */

export const MODE_RANK = { basic: 0, advanced: 1, admin: 2 };

/** Nav groups in display order. */
export const GROUPS = [
  { id: "workspace", label: "Workspace" },
  { id: "data", label: "Data" },
  { id: "retrieval", label: "Retrieval" },
  { id: "compute", label: "Compute" },
  { id: "platform", label: "Platform" },
  { id: "system", label: "System" },
  { id: "admin", label: "Administration", adminOnly: true },
];

/**
 * Route table. `minMode` = lowest mode in which the item appears in the rail
 * (deep-links still resolve). `view` = module basename under js/views/.
 */
export const ROUTES = [
  // Workspace
  { key: "home", label: "Home", icon: "layout-dashboard", group: "workspace", minMode: "basic", view: "home", title: "Home", desc: "Your local-first AI workspace at a glance." },
  { key: "chat", label: "Chat", icon: "message-2", group: "workspace", minMode: "basic", view: "chat", title: "Chat", desc: "Grounded conversation over your indexed workspace." },

  // Data
  { key: "files", label: "Files", icon: "folders", group: "data", minMode: "basic", view: "files", title: "Files", desc: "Connected sources and indexed documents." },

  // Retrieval (the product identity)
  { key: "hybrid-search", label: "Search", icon: "arrows-join", group: "retrieval", minMode: "basic", view: "hybrid-search", title: "Hybrid Search", desc: "Graph structure fused with vector similarity." },
  { key: "knowledge-graph", label: "Knowledge", icon: "chart-dots-3", group: "retrieval", minMode: "basic", view: "knowledge-graph", title: "Knowledge Graph", desc: "Your digital brain — every source converges here. Explore, ingest, and export." },
  { key: "memory", label: "Memory", icon: "brain", group: "retrieval", minMode: "basic", view: "memory", title: "Memory", desc: "Long-term workspace, project, agent, and conversation memory." },

  // Compute
  { key: "models", label: "Models", icon: "cpu", group: "compute", minMode: "basic", view: "models", title: "Models", desc: "Local MLX models and embeddings." },
  { key: "agents", label: "Agents", icon: "robot", group: "compute", minMode: "advanced", view: "agents", title: "Agents", desc: "Multi-agent roles, runs, and handoffs." },
  { key: "workflows", label: "Workflows", icon: "sitemap", group: "compute", minMode: "advanced", view: "workflows", title: "Workflow Agents", desc: "Trigger → agent chain → tools → memory → result." },

  // Platform (the agent ecosystem)
  { key: "skills", label: "Skills", icon: "puzzle", group: "platform", minMode: "advanced", view: "skills", title: "Skills", desc: "Install, enable, and manage skills." },
  { key: "hooks", label: "Hooks", icon: "webhook", group: "platform", minMode: "advanced", view: "hooks", title: "Hooks", desc: "Lifecycle hooks across runs, tools, and workflows." },
  { key: "mcp", label: "MCP", icon: "plug-connected", group: "platform", minMode: "advanced", view: "mcp", title: "MCP Manager", desc: "Connected MCP servers, available tools, and health." },

  // System
  { key: "settings", label: "Settings", icon: "settings", group: "system", minMode: "basic", view: "settings", title: "Settings", desc: "Appearance, workspace, and integrations." },

  // Deep-linkable legacy/experimental surfaces. They remain renderable for
  // compatibility, but are not promoted in the production navigation.
  { key: "pipeline", label: "Pipeline", icon: "git-branch", group: "data", minMode: "advanced", view: "pipeline", title: "Pipeline", desc: "Ingest, embed, and graph-build flows.", hidden: true },
  { key: "planning", label: "Planning", icon: "target-arrow", group: "compute", minMode: "advanced", view: "planning", title: "Autonomous Planning", desc: "Goal → plan → execute → review → replan.", hidden: true },
  { key: "my-computer", label: "My Computer", icon: "device-desktop-analytics", group: "compute", minMode: "advanced", view: "my-computer", title: "My Computer", desc: "Local hardware, memory, and runtime.", hidden: true },
  { key: "marketplace", label: "Marketplace", icon: "building-store", group: "platform", minMode: "advanced", view: "marketplace", title: "Marketplace", desc: "Agent templates, agents, plugins, and skills.", hidden: true },
  { key: "tools", label: "Tools", icon: "tools", group: "platform", minMode: "advanced", view: "tools", title: "Tool Registry", desc: "Local, workspace, and MCP tools with governance.", hidden: true },

  // Admin
  { key: "admin/users", label: "Users", icon: "users", group: "admin", minMode: "admin", view: "admin-users", title: "Users", desc: "Workspace members and access.", admin: true },
  { key: "admin/permissions", label: "Permissions", icon: "key", group: "admin", minMode: "admin", view: "admin-permissions", title: "Permissions", desc: "Roles and capability mapping.", admin: true },
  { key: "admin/audit", label: "Audit Logs", icon: "report-search", group: "admin", minMode: "admin", view: "admin-audit", title: "Audit Logs", desc: "Activity and access trail.", admin: true },
  { key: "admin/security", label: "Security", icon: "shield-check", group: "admin", minMode: "admin", view: "admin-security", title: "Security", desc: "Sensitive-data signals and DLP.", admin: true },
  { key: "admin/policies", label: "Policies", icon: "file-certificate", group: "admin", minMode: "admin", view: "admin-policies", title: "Policies", desc: "Governance and enforcement.", admin: true },
  { key: "admin/private-vpc", label: "Private VPC", icon: "cloud-lock", group: "admin", minMode: "admin", view: "admin-private-vpc", title: "Private VPC", desc: "Network isolation and peering.", admin: true },
];

export const ROUTE_BY_KEY = Object.fromEntries(ROUTES.map((r) => [r.key, r]));

/** Routes visible in the rail for a given mode. */
export function visibleRoutes(mode) {
  const rank = MODE_RANK[mode] ?? 0;
  return ROUTES.filter((r) => {
    if (r.hidden) return false;
    if (r.admin) return mode === "admin";
    return (MODE_RANK[r.minMode] ?? 0) <= rank;
  });
}

/** Lazy-load a view module by basename. Cached. */
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
