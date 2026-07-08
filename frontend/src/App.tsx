import * as React from "react";
import { type BrainState } from "@/components/LivingBrain";
import { ProductFlow, readProductFlowComplete } from "@/components/ProductFlow";
import { useAppStore } from "@/store/appStore";
import { parseHash, productShellRoutes } from "@/routes";
import { BrainHome } from "@/features/brain/BrainHome";
import { AdminConsole } from "@/features/admin/AdminConsole";
import { WorkspaceProfileSwitcher } from "@/components/WorkspaceProfileSwitcher";
import { AdminAccessGate } from "@/components/AdminAccessGate";
import { t, type Language } from "@/i18n";
import { latticeApi } from "@/api/client";
import { useQuery } from "@tanstack/react-query";
import { FeedbackState } from "@/components/FeedbackState";
import { Brain, Menu, X } from "lucide-react";
import { navigateHash } from "@/features/brain/navigation";
import { clamp } from "@/lib/utils";

const ActPage = React.lazy(() => import("@/pages/Act").then((module) => ({ default: module.ActPage })));
const BrainPage = React.lazy(() => import("@/pages/Brain").then((module) => ({ default: module.BrainPage })));
const CapturePage = React.lazy(() => import("@/pages/Capture").then((module) => ({ default: module.CapturePage })));
const LibraryPage = React.lazy(() => import("@/pages/Library").then((module) => ({ default: module.LibraryPage })));
const SystemPage = React.lazy(() => import("@/pages/System").then((module) => ({ default: module.SystemPage })));

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
          <React.Suspense fallback={<PageLoader language={language} />}>
            <ActPage initialTab={parsed.tab} />
          </React.Suspense>
        </BrainShell>
      ) : parsed.primary === "capture" ? (
        <BrainShell active={parsed.primary}>
          <React.Suspense fallback={<PageLoader language={language} />}>
            <CapturePage initialTab={parsed.tab} />
          </React.Suspense>
        </BrainShell>
      ) : parsed.primary === "library" ? (
        <BrainShell active={parsed.primary}>
          <React.Suspense fallback={<PageLoader language={language} />}>
            <LibraryPage initialTab={parsed.tab} />
          </React.Suspense>
        </BrainShell>
      ) : parsed.primary === "system" ? (
        <BrainShell active={parsed.primary}>
          <React.Suspense fallback={<PageLoader language={language} />}>
            <SystemPage initialTab={parsed.tab} />
          </React.Suspense>
        </BrainShell>
      ) : parsed.primary === "memory" ? (
        <BrainShell active="memory">
          <React.Suspense fallback={<PageLoader language={language} />}>
            <BrainPage initialTab="memory" />
          </React.Suspense>
        </BrainShell>
      ) : parsed.primary === "brain" && parsed.tab && parsed.tab !== "conversation" ? (
        <BrainShell active={parsed.tab === "graph" ? "memory" : "brain"}>
          <React.Suspense fallback={<PageLoader language={language} />}>
            <BrainPage initialTab={parsed.tab} />
          </React.Suspense>
        </BrainShell>
      ) : (
        <BrainShell active="brain">
          <BrainHome brainState={brainState} intensity={intensity} onBrainChange={setBrain} />
        </BrainShell>
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
  const language = useAppStore((state) => state.language);
  const [menuOpen, setMenuOpen] = React.useState(false);
  const menuButtonRef = React.useRef<HTMLButtonElement>(null);

  const closeMenu = React.useCallback(() => {
    setMenuOpen(false);
    menuButtonRef.current?.focus();
  }, []);

  React.useEffect(() => {
    if (!menuOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeMenu();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menuOpen, closeMenu]);

  const activeRoute = productShellRoutes.find((item) => item.id === active);

  return (
    <main className="brain-shell-page" aria-label="Lattice workspace">
      <header className="brain-topbar">
        <button
          ref={menuButtonRef}
          type="button"
          className="brain-menu-button"
          aria-expanded={menuOpen}
          aria-controls="brain-sidebar"
          aria-label={t(language, menuOpen ? "shell.menu.close" : "shell.menu.open")}
          onClick={() => setMenuOpen((open) => !open)}
        >
          {menuOpen ? <X aria-hidden="true" /> : <Menu aria-hidden="true" />}
        </button>
        <a className="brain-shell-brand" href="#/brain" aria-label={t(language, "brain.title")}>
          <span className="brain-shell-brand-mark" aria-hidden="true">
            <Brain aria-hidden="true" />
          </span>
          Lattice
        </a>
        {activeRoute && active !== "brain" ? (
          <span className="brain-topbar-crumb">{t(language, activeRoute.labelKey)}</span>
        ) : null}
      </header>

      {menuOpen ? <div className="brain-sidebar-scrim" onClick={closeMenu} aria-hidden="true" /> : null}
      <aside
        id="brain-sidebar"
        className={`brain-sidebar${menuOpen ? " is-open" : ""}`}
        aria-hidden={!menuOpen}
        aria-label={t(language, "shell.menu.title")}
      >
        <div className="brain-sidebar-head">
          <span className="brain-sidebar-title">{t(language, "shell.menu.title")}</span>
          <button
            type="button"
            className="brain-menu-button"
            aria-label={t(language, "shell.menu.close")}
            onClick={closeMenu}
          >
            <X aria-hidden="true" />
          </button>
        </div>
        <nav className="brain-sidebar-nav" aria-label={t(language, "shell.menu.nav")}>
          {productShellRoutes.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.id}
                type="button"
                className={`brain-sidebar-item${item.id === active ? " is-active" : ""}`}
                aria-current={item.id === active ? "page" : undefined}
                onClick={() => {
                  setMenuOpen(false);
                  navigateHash(`/${item.path}`);
                }}
              >
                {Icon && <Icon className="nav-icon" aria-hidden="true" />}
                <span>{t(language, item.labelKey)}</span>
              </button>
            );
          })}
        </nav>
        <div className="brain-sidebar-foot" aria-label={t(language, "shell.menu.workspace")}>
          <span className="brain-sidebar-section-label">{t(language, "shell.menu.workspace")}</span>
          <VsCodeSyncStatus language={language} />
          <WorkspaceProfileSwitcher language={language} />
          <AdminAccessGate language={language} />
          <ExternalConsentStatus language={language} />
        </div>
      </aside>

      <section className="brain-shell-content">{children}</section>
    </main>
  );
}

function ExternalConsentStatus({ language }: { language: Language }) {
  const externalConsent = useAppStore((state) => state.externalConsent);
  const setExternalConsent = useAppStore((state) => state.setExternalConsent);

  return (
    <section className="external-consent-status" aria-label={t(language, "feedback.consent.aria")}>
      <FeedbackState
        tone="empty"
        compact={externalConsent}
        language={language}
        title={externalConsent ? t(language, "feedback.consent.activeTitle") : t(language, "feedback.consent.revokedTitle")}
        body={externalConsent ? t(language, "feedback.consent.activeBody") : t(language, "feedback.consent.revokedBody")}
        actionLabel={externalConsent ? t(language, "feedback.consent.revoke") : t(language, "feedback.consent.reenable")}
        onAction={() => setExternalConsent(!externalConsent)}
      />
    </section>
  );
}

function VsCodeSyncStatus({ language }: { language: Language }) {
  const mode = useAppStore((state) => state.mode);
  // "VS Code" means nothing to a non-technical user. Only show the editor
  // sync indicator once someone has opted into advanced/admin mode.
  if (mode === "basic") return null;
  return <VsCodeSyncStatusInner language={language} />;
}

function VsCodeSyncStatusInner({ language }: { language: Language }) {
  const bridge = useQuery({
    queryKey: ["workspaceVscodeStatus"],
    queryFn: latticeApi.workspaceVscodeStatus,
    refetchInterval: 15000,
  });
  const index = useQuery({
    queryKey: ["index"],
    queryFn: latticeApi.indexStatus,
    refetchInterval: 15000,
  });
  const data = bridge.data?.data as Record<string, unknown> | undefined;
  const lastSeen = Number(data?.last_seen_ms || 0);
  const connected = Boolean(data?.connected) || (lastSeen > 0 && Date.now() - lastSeen < 60000);
  const indexData = index.data?.data as Record<string, unknown> | undefined;
  const indexStatus = String(indexData?.status || indexData?.state || "");
  const indexing = /index|build|running|pending/i.test(indexStatus);
  const state = bridge.isLoading
    ? "checking"
    : !connected
      ? "offline"
      : indexing
        ? "indexing"
        : "synced";
  const labelKey = `shell.sync.${state}`;
  const detail = connected
    ? t(language, "shell.sync.detail")
    : t(language, "shell.sync.detailOffline");

  return (
    <button
      type="button"
      className={`vscode-sync-status is-${state}`}
      aria-label={`${t(language, "shell.sync.label")}: ${t(language, labelKey)}`}
      title={`${t(language, "shell.sync.label")}: ${t(language, labelKey)}\n${detail}`}
      onClick={() => {
        window.location.hash = "/settings";
      }}
    >
      <span className="vscode-sync-dot" aria-hidden="true" />
      <span className="vscode-sync-copy">
        <strong>{t(language, "shell.sync.label")}</strong>
        <small>{t(language, labelKey)}</small>
      </span>
    </button>
  );
}

function PageLoader({ language }: { language: Language }) {
  return (
    <div className="brain-shell-loader" role="status">
      {t(language, "shell.loading")}
    </div>
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

function useBrainState() {
  const [state, setState] = React.useState<BrainState>("idle");
  const [intensity, setIntensity] = React.useState(0.58);

  const setBrain = React.useCallback((next: BrainState, nextIntensity?: number) => {
    setState(next);
    if (nextIntensity !== undefined) setIntensity(clamp(nextIntensity, 0.38, 1));
  }, []);

  return { state, intensity, setBrain };
}
