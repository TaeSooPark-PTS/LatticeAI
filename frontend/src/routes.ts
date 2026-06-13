import {
  Activity,
  Brain,
  Database,
  FolderInput,
  Library,
  MessageSquare,
  Network,
  Settings,
  Shield,
  Workflow,
  Zap,
} from "lucide-react";

export type PrimaryRoute = "brain" | "ask" | "capture" | "act" | "library" | "system";

export const primaryRoutes = [
  { id: "brain", label: "Brain", icon: Brain, description: "Explore memories and connections" },
  { id: "ask", label: "Ask", icon: MessageSquare, description: "Talk with remembered context" },
  { id: "capture", label: "Capture", icon: FolderInput, description: "Add files, folders, and pages" },
  { id: "act", label: "Act", icon: Workflow, description: "Run goals with approval" },
  { id: "library", label: "Library", icon: Library, description: "Models, skills, and tool connections" },
  { id: "system", label: "System", icon: Settings, description: "Account, backups, and safety" },
] as const;

export const routeAliases: Record<string, { primary: PrimaryRoute; tab?: string }> = {
  home: { primary: "brain", tab: "overview" },
  onboarding: { primary: "system", tab: "account" },
  "knowledge-graph": { primary: "brain", tab: "graph" },
  "hybrid-search": { primary: "brain", tab: "search" },
  memory: { primary: "brain", tab: "memory" },
  chat: { primary: "ask", tab: "chat" },
  files: { primary: "capture", tab: "files" },
  pipeline: { primary: "capture", tab: "pipeline" },
  "my-computer": { primary: "capture", tab: "local" },
  agents: { primary: "act", tab: "agents" },
  runs: { primary: "act", tab: "runs" },
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
  { key: "onboarding", label: "First Run", icon: Settings },
  { key: "knowledge-graph", label: "Brain Map", icon: Network },
  { key: "hybrid-search", label: "Brain Search", icon: Zap },
  { key: "memory", label: "Memory", icon: Database },
  { key: "chat", label: "Ask", icon: MessageSquare },
  { key: "files", label: "Capture Files", icon: FolderInput },
  { key: "agents", label: "Agents", icon: Workflow },
  { key: "workflows", label: "Workflows", icon: Workflow },
  { key: "models", label: "Models", icon: Library },
  { key: "network", label: "Brain Network", icon: Network },
  { key: "activity", label: "Activity", icon: Activity },
  { key: "admin/security", label: "Security", icon: Shield },
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
