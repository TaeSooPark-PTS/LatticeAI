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
  { id: "brain", label: "Home", icon: Brain, description: "The living map of what Lattice knows" },
  { id: "ask", label: "Ask", icon: MessageSquare, description: "Think with remembered context" },
  { id: "capture", label: "Add", icon: FolderInput, description: "Bring in files, folders, and pages" },
  { id: "act", label: "Automate", icon: Workflow, description: "Turn goals into supervised runs" },
  { id: "library", label: "Library", icon: Library, description: "Choose models, skills, and tools" },
  { id: "system", label: "Care", icon: Settings, description: "Keep your brain safe and portable" },
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
  { key: "brain", label: "Home", icon: Brain },
  { key: "onboarding", label: "First 10 Minutes", icon: Settings },
  { key: "knowledge-graph", label: "Memory Map", icon: Network },
  { key: "hybrid-search", label: "Search Everything", icon: Zap },
  { key: "memory", label: "Memory", icon: Database },
  { key: "chat", label: "Ask", icon: MessageSquare },
  { key: "files", label: "Add Files", icon: FolderInput },
  { key: "agents", label: "Start a Run", icon: Workflow },
  { key: "workflows", label: "Automations", icon: Workflow },
  { key: "models", label: "Models", icon: Library },
  { key: "network", label: "Trusted Devices", icon: Network },
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
