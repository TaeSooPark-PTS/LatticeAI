import {
  Brain,
  Database,
  FolderInput,
  Library,
  Settings,
  Workflow,
} from "lucide-react";

export type PrimaryRoute = "brain" | "memory" | "capture" | "act" | "library" | "system";

export const primaryRoutes = [
  { id: "brain", label: "Brain", icon: Brain, description: "Talk with your living Brain" },
  { id: "memory", label: "Memory", icon: Database, description: "Recall what your Brain remembers" },
  { id: "capture", label: "Files", icon: FolderInput, description: "Bring in files, folders, and pages" },
  { id: "act", label: "Automations", icon: Workflow, description: "Turn goals into supervised runs" },
  { id: "library", label: "Models", icon: Library, description: "Choose the local model powering your Brain" },
  { id: "system", label: "Settings", icon: Settings, description: "Keep your Brain safe and portable" },
] as const;

export const routeAliases: Record<string, { primary: PrimaryRoute; tab?: string }> = {
  home: { primary: "brain", tab: "conversation" },
  onboarding: { primary: "system", tab: "account" },
  "knowledge-graph": { primary: "brain", tab: "graph" },
  "hybrid-search": { primary: "brain", tab: "knowledge" },
  memory: { primary: "memory", tab: "memory" },
  ask: { primary: "brain", tab: "conversation" },
  chat: { primary: "brain", tab: "conversation" },
  files: { primary: "capture", tab: "files" },
  pipeline: { primary: "capture", tab: "pipeline" },
  "my-computer": { primary: "capture", tab: "local" },
  agents: { primary: "act", tab: "agents" },
  runs: { primary: "act", tab: "runs" },
  review: { primary: "act", tab: "review" },
  workflows: { primary: "act", tab: "workflows" },
  planning: { primary: "act", tab: "agents" },
  hooks: { primary: "act", tab: "hooks" },
  tools: { primary: "act", tab: "tools" },
  models: { primary: "library", tab: "models" },
  skills: { primary: "library", tab: "skills" },
  mcp: { primary: "library", tab: "mcp" },
  marketplace: { primary: "library", tab: "marketplace" },
  account: { primary: "system", tab: "account" },
  "workspace-admin": { primary: "system", tab: "workspaces" },
  snapshots: { primary: "system", tab: "snapshots" },
  activity: { primary: "system", tab: "activity" },
  network: { primary: "system", tab: "network" },
  settings: { primary: "system", tab: "settings" },
  "admin/users": { primary: "system", tab: "admin" },
  "admin/permissions": { primary: "system", tab: "admin" },
  "admin/audit": { primary: "system", tab: "admin" },
  "admin/security": { primary: "system", tab: "admin" },
  "admin/policies": { primary: "system", tab: "admin" },
  "admin/private-vpc": { primary: "system", tab: "admin" },
};

export const commandRoutes = [
  { key: "brain", label: "Brain", icon: Brain },
  { key: "memory", label: "Memory", icon: Database },
  { key: "files", label: "Files", icon: FolderInput },
  { key: "workflows", label: "Automations", icon: Workflow },
  { key: "models", label: "Models", icon: Library },
  { key: "settings", label: "Settings", icon: Settings },
];

export function parseHash() {
  const raw = window.location.hash.replace(/^#\/?/, "").replace(/^\/+/, "");
  const path = raw || "brain";
  const direct = primaryRoutes.find((route) => route.id === path);
  if (direct) return { primary: direct.id as PrimaryRoute, tab: undefined, path };
  const aliased = routeAliases[path] || routeAliases[path.split("?")[0]];
  return { primary: aliased?.primary || "brain", tab: aliased?.tab, path };
}

export function go(path: string) {
  window.location.hash = `/${path.replace(/^\/+/, "")}`;
}
