import * as React from "react";
import { type BrainState } from "@/components/LivingBrain";
import { ProductFlow, readProductFlowComplete } from "@/components/ProductFlow";
import { useAppStore } from "@/store/appStore";
import { parseHash } from "@/routes";
import { ActPage } from "@/pages/Act";
import { BrainPage } from "@/pages/Brain";
import { CapturePage } from "@/pages/Capture";
import { LibraryPage } from "@/pages/Library";
import { SystemPage } from "@/pages/System";
import { BrainHome } from "@/features/brain/BrainHome";
import { AdminConsole } from "@/features/admin/AdminConsole";

export default function App() {
  const theme = useAppStore((state) => state.theme);
  const language = useAppStore((state) => state.language);
  const [flowComplete, setFlowComplete] = React.useState(readProductFlowComplete);
  const rawRoute = useHashRoute();
  const parsed = React.useMemo(() => parseHash(), [rawRoute]);
  const { state: brainState, intensity, setBrain } = useBrainState();

  React.useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.documentElement.lang = language === "ko" ? "ko" : "en";
  }, [theme, language]);

  React.useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        document.querySelector<HTMLTextAreaElement>(".brain-composer textarea")?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (!flowComplete) {
    return <ProductFlow onComplete={() => setFlowComplete(true)} />;
  }

  return (
    <div className="brain-space">
      <div className="brain-field" />
      {rawRoute.startsWith("/admin") ? (
        <AdminConsole onBack={() => navigateHash("/brain")} />
      ) : parsed.primary === "act" ? (
        <BrainShell active={parsed.primary}>
          <ActPage initialTab={parsed.tab} />
        </BrainShell>
      ) : parsed.primary === "capture" ? (
        <BrainShell active={parsed.primary}>
          <CapturePage initialTab={parsed.tab} />
        </BrainShell>
      ) : parsed.primary === "library" ? (
        <BrainShell active={parsed.primary}>
          <LibraryPage initialTab={parsed.tab} />
        </BrainShell>
      ) : parsed.primary === "system" ? (
        <BrainShell active={parsed.primary}>
          <SystemPage initialTab={parsed.tab} />
        </BrainShell>
      ) : parsed.primary === "memory" ? (
        <BrainShell active="memory">
          <BrainPage initialTab="memory" />
        </BrainShell>
      ) : parsed.primary === "brain" && parsed.tab && parsed.tab !== "conversation" ? (
        <BrainShell active="brain">
          <BrainPage initialTab={parsed.tab} />
        </BrainShell>
      ) : (
        <BrainHome brainState={brainState} intensity={intensity} onBrainChange={setBrain} />
      )}
    </div>
  );
}

function BrainShell({
  active,
  children,
}: {
  active: string;
  children: React.ReactNode;
}) {
  const items = [
    { id: "brain", label: "Brain", path: "/brain" },
    { id: "capture", label: "Files", path: "/capture" },
    { id: "memory", label: "Graph", path: "/knowledge-graph" },
    { id: "library", label: "Models", path: "/models" },
    { id: "system", label: "Settings", path: "/settings" },
    { id: "act", label: "Act", path: "/review" },
  ];
  return (
    <main className="brain-shell-page" aria-label="Lattice workspace">
      <nav className="brain-shell-nav" aria-label="Brain workspace navigation">
        {items.map((item) => (
          <button
            key={item.id}
            type="button"
            className={item.id === active ? "is-active" : ""}
            onClick={() => navigateHash(item.path)}
          >
            {item.label}
          </button>
        ))}
      </nav>
      <section className="brain-shell-content">
        {children}
      </section>
    </main>
  );
}

function useHashRoute() {
  const read = React.useCallback(() => {
    const hash = window.location.hash.replace(/^#/, "");
    return hash.startsWith("/") ? hash : "/brain";
  }, []);
  const [route, setRoute] = React.useState(read);

  React.useEffect(() => {
    const onHashChange = () => setRoute(read());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, [read]);

  return route;
}

function navigateHash(route: string) {
  window.location.hash = route;
}

function useBrainState() {
  const [state, setState] = React.useState<BrainState>("idle");
  const [intensity, setIntensity] = React.useState(0.58);

  const setBrain = React.useCallback((next: BrainState, nextIntensity?: number) => {
    setState(next);
    if (nextIntensity !== undefined) setIntensity(clamp(nextIntensity, 0.38, 1));
  }, []);

  return { state, intensity, setBrain };
}

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
}
