import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { Command, Menu, Moon, Search, Sparkles, Sun, X } from "lucide-react";
import { latticeApi } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { FirstRunGuide } from "@/components/FirstRunGuide";
import { useAppStore } from "@/store/appStore";
import { commandRoutes, go, parseHash, primaryRoutes, PrimaryRoute } from "@/routes";
import { BrainPage } from "@/pages/Brain";
import { AskPage } from "@/pages/Ask";
import { CapturePage } from "@/pages/Capture";
import { ActPage } from "@/pages/Act";
import { LibraryPage } from "@/pages/Library";
import { SystemPage } from "@/pages/System";
import { cn } from "@/lib/utils";

function useRoute() {
  const [route, setRoute] = React.useState(parseHash);
  React.useEffect(() => {
    const onHash = () => setRoute(parseHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  return route;
}

function Page({ primary, tab }: { primary: PrimaryRoute; tab?: string }) {
  if (primary === "ask") return <AskPage />;
  if (primary === "capture") return <CapturePage initialTab={tab} />;
  if (primary === "act") return <ActPage initialTab={tab} />;
  if (primary === "library") return <LibraryPage initialTab={tab} />;
  if (primary === "system") return <SystemPage initialTab={tab} />;
  return <BrainPage initialTab={tab} />;
}

function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [query, setQuery] = React.useState("");
  const matches = commandRoutes.filter((route) => route.label.toLowerCase().includes(query.toLowerCase()) || route.key.includes(query.toLowerCase()));
  React.useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 bg-background/76 p-4 backdrop-blur-xl" role="dialog" aria-modal="true">
      <div className="premium-surface mx-auto mt-16 max-w-2xl overflow-hidden rounded-lg">
        <div className="flex items-center gap-2 border-b border-border p-3">
          <Search className="h-4 w-4 text-muted-foreground" />
          <Input value={query} onChange={(e) => setQuery(e.target.value)} autoFocus placeholder="Search Lattice" />
          <Button variant="ghost" size="icon" onClick={onClose}><X className="h-4 w-4" /></Button>
        </div>
        <div className="soft-scrollbar max-h-96 overflow-auto p-2">
          {matches.map((route) => {
            const Icon = route.icon;
            return (
              <button
                key={route.key}
                onClick={() => {
                  go(route.key);
                  onClose();
                }}
                className="flex w-full items-center gap-3 rounded-md px-3 py-3 text-left text-sm hover:bg-muted"
              >
                <span className="grid h-8 w-8 place-items-center rounded-md bg-primary/12 text-primary"><Icon className="h-4 w-4" /></span>
                <span className="font-medium">{route.label}</span>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}

export default function App() {
  const route = useRoute();
  const { theme, setTheme, mode, setMode } = useAppStore();
  const [drawer, setDrawer] = React.useState(false);
  const [palette, setPalette] = React.useState(false);
  const health = useQuery({ queryKey: ["health"], queryFn: latticeApi.health });
  const desktop = useQuery({
    queryKey: ["desktopBackendStatus"],
    queryFn: latticeApi.desktopBackendStatus,
    enabled: Boolean(window.__TAURI_INTERNALS__),
    refetchInterval: 5000,
  });
  const workspace = useQuery({ queryKey: ["workspaceOs"], queryFn: latticeApi.workspaceOs });

  React.useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);
  React.useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPalette(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const healthData = (health.data?.data || {}) as Record<string, unknown>;
  const appVersion = typeof healthData.version === "string" ? healthData.version : null;
  const desktopData = (desktop.data?.data || {}) as Record<string, unknown>;
  const desktopError = typeof desktopData.last_error === "string" ? desktopData.last_error : desktop.data?.error;

  const activeRoute = primaryRoutes.find((item) => item.id === route.primary);
  const rail = (
    <aside className="flex h-full w-72 shrink-0 flex-col border-r border-border bg-card/88 backdrop-blur-xl">
      <div className="flex h-20 items-center gap-3 border-b border-border px-4">
        <div className="grid h-10 w-10 place-items-center rounded-lg bg-primary text-primary-foreground font-black">LA</div>
        <div>
          <div className="font-semibold leading-tight">Lattice AI</div>
          <div className="text-xs text-muted-foreground">Your Digital Brain</div>
        </div>
      </div>
      <nav className="soft-scrollbar flex-1 space-y-1 overflow-auto p-3">
        {primaryRoutes.map((item) => {
          const Icon = item.icon;
          const active = route.primary === item.id;
          return (
            <button
              key={item.id}
              onClick={() => {
                go(item.id);
                setDrawer(false);
              }}
              className={cn(
                "flex min-h-16 w-full items-center gap-3 rounded-lg px-3 py-2 text-left transition",
                active ? "bg-primary/12 text-foreground shadow-sm" : "text-muted-foreground hover:bg-muted/70 hover:text-foreground",
              )}
            >
              <span className={cn("grid h-9 w-9 place-items-center rounded-md border border-border", active ? "bg-primary text-primary-foreground" : "bg-background/55")}>
                <Icon className="h-4 w-4" />
              </span>
              <span className="min-w-0">
                <span className="block text-sm font-medium">{item.label}</span>
                <span className="block truncate text-xs">{item.description}</span>
              </span>
            </button>
          );
        })}
      </nav>
      <div className="border-t border-border p-4 text-xs text-muted-foreground">
        <div className="mb-2 flex items-center gap-2 text-foreground">
          <span className={cn("h-2 w-2 rounded-full", health.data?.ok ? "bg-emerald-400" : "bg-amber-400")} />
          {health.data?.ok ? "Ready on this Mac" : "Starting up"}
        </div>
        {window.__TAURI_INTERNALS__ ? (
          <div>Desktop bridge: {desktopData.running ? "ready" : desktopError ? "needs attention" : "starting"}</div>
        ) : null}
        <div>Workspace: {String((workspace.data?.data as Record<string, unknown>)?.active_workspace || "local")}</div>
      </div>
    </aside>
  );

  return (
    <div className="app-backdrop min-h-screen text-foreground">
      <CommandPalette open={palette} onClose={() => setPalette(false)} />
      <div className="hidden lg:fixed lg:inset-y-0 lg:left-0 lg:block">{rail}</div>
      {drawer ? (
        <div className="fixed inset-0 z-40 lg:hidden">
          <button className="absolute inset-0 bg-background/70" aria-label="Close navigation" onClick={() => setDrawer(false)} />
          <div className="relative h-full">{rail}</div>
        </div>
      ) : null}
      <div className="lg:pl-72">
        <header className="sticky top-0 z-30 flex h-20 items-center justify-between gap-3 border-b border-border bg-background/78 px-4 backdrop-blur-xl lg:px-6">
          <div className="flex min-w-0 items-center gap-2">
            <Button variant="ghost" size="icon" className="lg:hidden" onClick={() => setDrawer(true)}><Menu className="h-5 w-5" /></Button>
            <div className="flex min-w-0 items-center gap-3">
              <div className="hidden h-10 w-10 place-items-center rounded-lg border border-border bg-card sm:grid">
                <Sparkles className="h-4 w-4 text-primary" />
              </div>
              <div className="min-w-0">
                <div className="truncate text-xs text-muted-foreground">{appVersion ? `v${appVersion}` : "Version unavailable"}</div>
                <div className="truncate text-base font-semibold">{activeRoute?.label}</div>
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={() => setPalette(true)}><Command className="h-4 w-4" /> Search</Button>
            <select
              value={mode}
              onChange={(e) => setMode(e.target.value as "basic" | "advanced" | "admin")}
              className="h-10 rounded-md border border-border bg-card/70 px-3 text-sm font-semibold"
              aria-label="Workspace mode"
            >
              <option value="basic">Basic</option>
              <option value="advanced">Advanced</option>
              <option value="admin">Admin</option>
            </select>
            <Button variant="outline" size="icon" onClick={() => setTheme(theme === "dark" ? "light" : "dark")} aria-label="Toggle theme">
              {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </Button>
          </div>
        </header>
        <main className="page-shell p-4 pb-12 lg:p-6 lg:pb-16">
          <FirstRunGuide />
          <Page primary={route.primary} tab={route.tab} />
        </main>
      </div>
    </div>
  );
}
