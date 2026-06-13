import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { BrainCircuit, CheckCircle2, Command, Menu, Moon, Search, Sparkles, Sun, X } from "lucide-react";
import { latticeApi } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { FirstRunGuide } from "@/components/FirstRunGuide";
import { useAppStore, WorkspaceMode } from "@/store/appStore";
import { commandRoutes, go, parseHash, primaryRoutes, PrimaryRoute } from "@/routes";
import { BrainPage } from "@/pages/Brain";
import { AskPage } from "@/pages/Ask";
import { CapturePage } from "@/pages/Capture";
import { ActPage } from "@/pages/Act";
import { LibraryPage } from "@/pages/Library";
import { SystemPage } from "@/pages/System";
import { cn } from "@/lib/utils";

const modes: Array<{ id: WorkspaceMode; label: string }> = [
  { id: "basic", label: "Calm" },
  { id: "advanced", label: "Deep" },
  { id: "admin", label: "Admin" },
];

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

function AmbientBrain() {
  return (
    <div className="ambient-brain" aria-hidden="true">
      <span className="signal-line signal-line-a" />
      <span className="signal-line signal-line-b" />
      <span className="signal-line signal-line-c" />
      <span className="signal-tile signal-tile-a" />
      <span className="signal-tile signal-tile-b" />
      <span className="signal-tile signal-tile-c" />
    </div>
  );
}

function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [query, setQuery] = React.useState("");
  const normalized = query.trim().toLowerCase();
  const matches = commandRoutes.filter((route) => (
    route.label.toLowerCase().includes(normalized) || route.key.includes(normalized)
  ));

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
    <div className="command-scrim" role="dialog" aria-modal="true" aria-label="Lattice command palette">
      <div className="command-panel">
        <div className="command-search">
          <Search className="h-4 w-4 text-muted-foreground" />
          <Input value={query} onChange={(event) => setQuery(event.target.value)} autoFocus placeholder="Jump to anything in Lattice" />
          <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close command palette"><X className="h-4 w-4" /></Button>
        </div>
        <div className="command-list soft-scrollbar">
          {matches.map((route) => {
            const Icon = route.icon;
            return (
              <button
                key={route.key}
                onClick={() => {
                  go(route.key);
                  onClose();
                }}
                className="command-row"
              >
                <span className="command-icon"><Icon className="h-4 w-4" /></span>
                <span>
                  <span className="block text-sm font-semibold">{route.label}</span>
                  <span className="block text-xs text-muted-foreground">Open {route.key.replace(/[-/]/g, " ")}</span>
                </span>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function PrimaryDock({ active, onNavigate }: { active: PrimaryRoute; onNavigate?: () => void }) {
  return (
    <nav className="primary-dock" aria-label="Primary navigation">
      {primaryRoutes.map((item) => {
        const Icon = item.icon;
        const selected = active === item.id;
        return (
          <button
            key={item.id}
            className={cn("dock-button", selected && "is-active")}
            onClick={() => {
              go(item.id);
              onNavigate?.();
            }}
            aria-current={selected ? "page" : undefined}
          >
            <Icon className="h-4 w-4" />
            <span>{item.label}</span>
          </button>
        );
      })}
    </nav>
  );
}

function ModeSwitch({ mode, setMode }: { mode: WorkspaceMode; setMode: (mode: WorkspaceMode) => void }) {
  return (
    <div className="mode-switch" aria-label="Experience mode">
      {modes.map((item) => (
        <button
          key={item.id}
          className={cn(mode === item.id && "is-active")}
          onClick={() => setMode(item.id)}
          aria-pressed={mode === item.id}
        >
          {item.label}
        </button>
      ))}
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
  const workspaceData = (workspace.data?.data || {}) as Record<string, unknown>;
  const desktopData = (desktop.data?.data || {}) as Record<string, unknown>;
  const appVersion = typeof healthData.version === "string" ? healthData.version : null;
  const activeRoute = primaryRoutes.find((item) => item.id === route.primary);
  const workspaceName = String(workspaceData.active_workspace || "Personal space");
  const backendReady = Boolean(health.data?.ok);
  const desktopReady = !window.__TAURI_INTERNALS__ || Boolean(desktopData.running);

  return (
    <div className="app-backdrop min-h-screen text-foreground">
      <AmbientBrain />
      <CommandPalette open={palette} onClose={() => setPalette(false)} />

      <header className="app-chrome">
        <div className="brand-lockup">
          <button className="mobile-menu" onClick={() => setDrawer(true)} aria-label="Open navigation"><Menu className="h-5 w-5" /></button>
          <button className="brand-mark" onClick={() => go("brain")} aria-label="Open Lattice home">
            <BrainCircuit className="h-5 w-5" />
          </button>
          <div className="brand-copy">
            <div className="brand-name">Lattice</div>
            <div className="brand-subtitle">Digital Brain</div>
          </div>
        </div>

        <div className="desktop-dock">
          <PrimaryDock active={route.primary} />
        </div>

        <div className="chrome-actions">
          <button className="status-chip" onClick={() => go("settings")}>
            <span className={cn("status-light", backendReady && desktopReady ? "is-ready" : "is-waiting")} />
            <span>{backendReady && desktopReady ? "Ready" : "Starting"}</span>
          </button>
          <Button variant="outline" onClick={() => setPalette(true)}><Command className="h-4 w-4" /> Find</Button>
          <Button variant="outline" size="icon" onClick={() => setTheme(theme === "dark" ? "light" : "dark")} aria-label="Toggle theme">
            {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </Button>
        </div>
      </header>

      {drawer ? (
        <div className="mobile-drawer">
          <button className="drawer-scrim" aria-label="Close navigation" onClick={() => setDrawer(false)} />
          <div className="drawer-panel">
            <div className="drawer-header">
              <div>
                <div className="font-semibold">Lattice</div>
                <div className="text-xs text-muted-foreground">Choose a room</div>
              </div>
              <Button variant="ghost" size="icon" onClick={() => setDrawer(false)} aria-label="Close navigation"><X className="h-4 w-4" /></Button>
            </div>
            <PrimaryDock active={route.primary} onNavigate={() => setDrawer(false)} />
          </div>
        </div>
      ) : null}

      <main className="page-shell">
        <section className="workspace-ribbon" aria-label="Current workspace">
          <div className="min-w-0">
            <div className="ribbon-kicker"><Sparkles className="h-4 w-4" /> {activeRoute?.label || "Home"}</div>
            <h1>{activeRoute?.description || "A calm place to think with your knowledge."}</h1>
          </div>
          <div className="ribbon-meta">
            <div className="meta-card">
              <CheckCircle2 className="h-4 w-4 text-primary" />
              <span>{workspaceName}</span>
            </div>
            <div className="meta-card">
              <span>{appVersion ? `v${appVersion}` : "Version checking"}</span>
            </div>
            <ModeSwitch mode={mode} setMode={setMode} />
          </div>
        </section>

        <FirstRunGuide />
        <Page primary={route.primary} tab={route.tab} />
      </main>
    </div>
  );
}
