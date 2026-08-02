import {
  Brain,
  Database,
  FileSearch,
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
  // Points at `review`, not `agents`: the Work screen now leads with what is
  // waiting on the person, so the shell entry point has to land on the review
  // inbox rather than on the goal composer sitting behind it.
  { id: "act", path: "review", labelKey: "shell.route.act", icon: Workflow, description: "Start work and review what needs attention" },
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

// The command palette's page list. This used to be a second, parallel copy of
// the same destinations that the palette did not read — so re-pointing "작업"
// here changed nothing, while the list the palette actually rendered kept the
// old target. One list, read by the palette, is what keeps a label from meaning
// two different screens depending on how you reached it.
export const commandRoutes = [
  { id: "page-brain", labelKey: "shell.route.brain", target: "/brain", icon: Brain },
  { id: "page-capture", labelKey: "shell.route.capture", target: "/capture", icon: FolderInput },
  { id: "page-memory", labelKey: "shell.route.memory", target: "/hybrid-search", icon: Database },
  { id: "page-library", labelKey: "shell.route.library", target: "/models", icon: Library },
  // Same destination as the shell's "작업" link, which now opens the review
  // inbox. Leaving this at /agents gave one label two landing places.
  { id: "page-act", labelKey: "shell.route.act", target: "/review", icon: Workflow },
  { id: "page-review", labelKey: "command.page.review", target: "/act/review", icon: FileSearch },
  { id: "page-system", labelKey: "shell.route.system", target: "/settings", icon: Settings },
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
  if (aliased) return { primary: aliased.primary, tab: aliased.tab, path };
  // `<primary>/<tab>` — the shape the palette and the daily briefing have always
  // emitted for /act/review, /act/workflows and /brain/graph. Nothing resolved
  // it, so every one of those landed on the Brain home instead of the screen it
  // named. Checked last, so a named alias always wins over the generic form.
  const separator = normalized.indexOf("/");
  if (separator > 0) {
    const head = normalized.slice(0, separator);
    const tail = normalized.slice(separator + 1);
    const nested = productShellRoutes.find((route) => route.id === head);
    if (nested && tail) return { primary: nested.id as PrimaryRoute, tab: tail, path };
  }
  return { primary: "brain" as PrimaryRoute, tab: undefined, path };
}

export function go(path: string) {
  window.location.hash = `/${path.replace(/^\/+/, "")}`;
}
