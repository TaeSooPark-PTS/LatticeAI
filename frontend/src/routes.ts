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

export type PrimaryRoute = "brain" | "capture" | "act" | "library" | "system";

export const primaryRoutes = [
  { id: "brain", label: "Brain", icon: Brain, description: "Talk with your living Brain" },
  { id: "capture", label: "Add", icon: FolderInput, description: "Bring in files, folders, and pages" },
  { id: "act", label: "Automate", icon: Workflow, description: "Turn goals into supervised runs" },
  { id: "library", label: "Library", icon: Library, description: "Choose models, skills, and tools" },
  { id: "system", label: "Care", icon: Settings, description: "Keep your brain safe and portable" },
] as const;

export const routeAliases: Record<string, { primary: PrimaryRoute; tab?: string }> = {
  home: { primary: "brain", tab: "conversation" },
  onboarding: { primary: "system", tab: "account" },
  "knowledge-graph": { primary: "brain", tab: "graph" },
  "hybrid-search": { primary: "brain", tab: "knowledge" },
  memory: { primary: "brain", tab: "memory" },
  ask: { primary: "brain", tab: "conversation" },
  chat: { primary: "brain", tab: "conversation" },
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
  { key: "onboarding", label: "First 10 Minutes", icon: Settings },
  { key: "chat", label: "Talk to Brain", icon: MessageSquare },
  { key: "memory", label: "Memories", icon: Database },
  { key: "hybrid-search", label: "Knowledge Search", icon: Zap },
  { key: "knowledge-graph", label: "Advanced Graph", icon: Network },
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
