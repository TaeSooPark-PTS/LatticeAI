import {
  Brain,
  Database,
  FolderInput,
  Library,
  Settings,
  Workflow,
} from "lucide-react";

export type PrimaryRoute = "brain" | "memory" | "capture" | "act" | "library" | "system";

export type RouteTarget = { primary: PrimaryRoute; tab?: string };

export const productShellRoutes = [
  { id: "brain", path: "brain", labelKey: "shell.route.brain", icon: Brain, description: "Talk with your living Brain" },
  { id: "capture", path: "capture", labelKey: "shell.route.capture", icon: FolderInput, description: "Bring in files, folders, and pages" },
  { id: "memory", path: "hybrid-search", labelKey: "shell.route.memory", icon: Database, description: "Search and revisit remembered knowledge" },
  { id: "library", path: "models", labelKey: "shell.route.library", icon: Library, description: "Choose the local model powering your Brain" },
  { id: "act", path: "agents", labelKey: "shell.route.act", icon: Workflow, description: "Start work and review what needs attention" },
  { id: "system", path: "settings", labelKey: "shell.route.system", icon: Settings, description: "Keep your Brain safe and portable" },
] as const;

export const directProductRoutes: Record<string, RouteTarget> = {
  brain: { primary: "brain", tab: "conversation" },
  capture: { primary: "capture", tab: "files" },
  "knowledge-graph": { primary: "brain", tab: "graph" },
  models: { primary: "library", tab: "models" },
  settings: { primary: "system", tab: "settings" },
  review: { primary: "act", tab: "review" },
};

export const compatibilityRouteAliases: Record<string, RouteTarget> = {
  home: { primary: "brain", tab: "conversation" },
  onboarding: { primary: "system", tab: "account" },
  "hybrid-search": { primary: "brain", tab: "knowledge" },
  memory: { primary: "memory", tab: "memory" },
  ask: { primary: "brain", tab: "conversation" },
  chat: { primary: "brain", tab: "conversation" },
  files: { primary: "capture", tab: "files" },
  pipeline: { primary: "capture", tab: "pipeline" },
  "capture-browser": { primary: "capture", tab: "browser" },
  "my-computer": { primary: "capture", tab: "local" },
  agents: { primary: "act", tab: "agents" },
  runs: { primary: "act", tab: "runs" },
  "review-center": { primary: "act", tab: "review" },
  workflows: { primary: "act", tab: "workflows" },
  planning: { primary: "act", tab: "agents" },
  hooks: { primary: "act", tab: "hooks" },
  tools: { primary: "act", tab: "tools" },
  skills: { primary: "library", tab: "skills" },
  mcp: { primary: "library", tab: "mcp" },
  marketplace: { primary: "library", tab: "marketplace" },
  account: { primary: "system", tab: "account" },
  "workspace-admin": { primary: "system", tab: "workspaces" },
  snapshots: { primary: "system", tab: "snapshots" },
  activity: { primary: "system", tab: "activity" },
  network: { primary: "system", tab: "network" },
  settings: { primary: "system", tab: "settings" },
  "system-admin": { primary: "system", tab: "admin" },
  "admin/users": { primary: "system", tab: "admin" },
  "admin/permissions": { primary: "system", tab: "admin" },
  "admin/audit": { primary: "system", tab: "admin" },
  "admin/security": { primary: "system", tab: "admin" },
  "admin/policies": { primary: "system", tab: "admin" },
  "admin/private-vpc": { primary: "system", tab: "admin" },
};

export const primaryRoutes = productShellRoutes;
export const routeAliases = compatibilityRouteAliases;

export const commandRoutes = [
  { key: "brain", label: "Lattice Brain", icon: Brain },
  { key: "files", label: "Sources", icon: FolderInput },
  { key: "hybrid-search", label: "Memory", icon: Database },
  { key: "models", label: "AI model", icon: Library },
  { key: "settings", label: "Settings", icon: Settings },
  { key: "agents", label: "Work", icon: Workflow },
];

export function parseHash() {
  const raw = window.location.hash.replace(/^#\/?/, "").replace(/^\/+/, "");
  const path = raw || "brain";
  const normalized = path.split("?")[0];
  const direct = directProductRoutes[normalized];
  if (direct) return { ...direct, path };
  const primary = productShellRoutes.find((route) => route.id === normalized);
  if (primary) return { primary: primary.id as PrimaryRoute, tab: undefined, path };
  const aliased = compatibilityRouteAliases[normalized];
  return { primary: aliased?.primary || "brain", tab: aliased?.tab, path };
}

export function go(path: string) {
  window.location.hash = `/${path.replace(/^\/+/, "")}`;
}
