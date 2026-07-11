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
import { Brain, Ellipsis, X } from "lucide-react";
import { navigateHash } from "@/features/brain/navigation";
import { clamp } from "@/lib/utils";
import { CoreServiceUnavailableBanner } from "@/components/CoreServiceUnavailableBanner";

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
        const composer = document.querySelector<HTMLTextAreaElement>(".brain-composer textarea");
        if (composer) {
          composer.focus();
          return;
        }
        navigateHash("/brain");
        window.setTimeout(() => {
          document.querySelector<HTMLTextAreaElement>(".brain-composer textarea")?.focus();
        }, 0);
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
      <CoreServiceUnavailableBanner />
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
        <BrainShell active={parsed.tab === "graph" || parsed.tab === "knowledge" || parsed.tab === "memory" ? "memory" : "brain"}>
          <React.Suspense fallback={<PageLoader language={language} />}>
            <BrainPage initialTab={parsed.tab} />
          </React.Suspense>
        </BrainShell>
      ) : (
        <BrainShell active="brain" contentOwnsMain>
          <BrainHome brainState={brainState} intensity={intensity} onBrainChange={setBrain} />
        </BrainShell>
      )}
    </div>
  );
}

function BrainShell({
  active,
  contentOwnsMain = false,
  children,
}: {
  active: string;
  contentOwnsMain?: boolean;
  children: React.ReactNode;
}) {
  const language = useAppStore((state) => state.language);
  const [menuOpen, setMenuOpen] = React.useState(false);
  const menuPanelRef = React.useRef<HTMLDivElement>(null);
  const desktopMenuButtonRef = React.useRef<HTMLButtonElement>(null);
  const mobileMenuButtonRef = React.useRef<HTMLButtonElement>(null);
  const returnFocusRef = React.useRef<HTMLButtonElement | null>(null);

  const closeMenu = React.useCallback((restoreFocus = true) => {
    setMenuOpen(false);
    if (restoreFocus) {
      window.requestAnimationFrame(() => returnFocusRef.current?.focus());
    }
  }, []);

  const toggleMenu = React.useCallback((trigger: HTMLButtonElement) => {
    returnFocusRef.current = trigger;
    setMenuOpen((open) => !open);
  }, []);

  React.useEffect(() => {
    if (!menuOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeMenu();
        return;
      }
      if (event.key === "Tab") {
        const focusable = Array.from(
          menuPanelRef.current?.querySelectorAll<HTMLElement>(
            'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
          ) ?? [],
        );
        if (!focusable.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (
        menuPanelRef.current?.contains(target)
        || desktopMenuButtonRef.current?.contains(target)
        || mobileMenuButtonRef.current?.contains(target)
      ) {
        return;
      }
      closeMenu(false);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onPointerDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onPointerDown);
    };
  }, [menuOpen, closeMenu]);

  React.useEffect(() => {
    setMenuOpen(false);
  }, [active]);

  React.useEffect(() => {
    if (!menuOpen) return;
    window.requestAnimationFrame(() => {
      const firstNavigationItem = menuPanelRef.current?.querySelector<HTMLElement>(".brain-more-nav a[href]");
      const fallbackButton = menuPanelRef.current?.querySelector<HTMLElement>("button:not([disabled])");
      (firstNavigationItem || fallbackButton)?.focus();
    });
  }, [menuOpen]);

  const primaryRoutes = productShellRoutes.filter((item) => (
    item.id === "brain" || item.id === "capture" || item.id === "memory" || item.id === "act"
  ));
  const secondaryRoutes = productShellRoutes.filter((item) => (
    item.id === "library" || item.id === "system"
  ));
  const Content = contentOwnsMain ? "div" : "main";
  const skipLabel = t(language, "shell.skip");

  return (
    <div className="brain-shell-page">
      <a
        className="brain-skip-link"
        href="#brain-main-content"
        onClick={(event) => {
          event.preventDefault();
          document.getElementById("brain-main-content")?.focus();
        }}
      >
        {skipLabel}
      </a>

      <header className="brain-topbar">
        <a className="brain-shell-brand" href="#/brain" aria-label={t(language, "brain.title")}>
          <span className="brain-shell-brand-mark" aria-hidden="true">
            <Brain aria-hidden="true" />
          </span>
          Lattice
        </a>

        <nav className="brain-desktop-nav brain-primary-nav" aria-label={t(language, "shell.menu.nav")}>
          {primaryRoutes.map((item) => {
            const Icon = item.icon;
            return (
              <a
                key={item.id}
                className={`brain-nav-link${item.id === active ? " is-active" : ""}`}
                href={`#/${item.path}`}
                aria-current={item.id === active ? "page" : undefined}
              >
                {Icon && <Icon className="nav-icon" aria-hidden="true" />}
                <span>{t(language, item.labelKey)}</span>
              </a>
            );
          })}
        </nav>

        <div className="brain-shell-actions">
          <button
            ref={desktopMenuButtonRef}
            type="button"
            className={`brain-more-button brain-menu-button${menuOpen ? " is-open" : ""}`}
            aria-expanded={menuOpen}
            aria-controls="brain-more-popover"
            aria-haspopup="dialog"
            aria-label={t(language, menuOpen ? "shell.menu.close" : "shell.menu.open")}
            onClick={(event) => toggleMenu(event.currentTarget)}
          >
            <Ellipsis aria-hidden="true" />
            <span>{t(language, "shell.menu.title")}</span>
          </button>
        </div>
      </header>

      {menuOpen ? (
        <>
          <div className="brain-sidebar-scrim brain-more-scrim" aria-hidden="true" />
          <div
            ref={menuPanelRef}
            id="brain-more-popover"
            className="brain-more-popover"
            role="dialog"
            aria-label={t(language, "shell.menu.title")}
          >
            <div className="brain-more-popover-head">
              <strong>{t(language, "shell.menu.title")}</strong>
              <button
                type="button"
                className="brain-menu-button"
                aria-label={t(language, "shell.menu.close")}
                onClick={() => closeMenu()}
              >
                <X aria-hidden="true" />
              </button>
            </div>

            <section className="brain-more-section" aria-labelledby="brain-more-navigation-label">
              <span id="brain-more-navigation-label" className="brain-more-section-label">
                {t(language, "shell.menu.nav")}
              </span>
              <nav className="brain-more-nav" aria-label={t(language, "shell.menu.nav")}>
                {secondaryRoutes.map((item) => {
                  const Icon = item.icon;
                  return (
                    <a
                      key={item.id}
                      className={`brain-more-nav-item brain-sidebar-item${item.id === active ? " is-active" : ""}`}
                      href={`#/${item.path}`}
                      aria-current={item.id === active ? "page" : undefined}
                      onClick={() => closeMenu(false)}
                    >
                      {Icon && <Icon className="nav-icon" aria-hidden="true" />}
                      <span>{t(language, item.labelKey)}</span>
                    </a>
                  );
                })}
              </nav>
            </section>

            <section
              className="brain-more-section brain-more-utilities brain-sidebar-foot"
              aria-labelledby="brain-more-workspace-label"
            >
              <span id="brain-more-workspace-label" className="brain-more-section-label brain-sidebar-section-label">
                {t(language, "shell.menu.workspace")}
              </span>
              <WorkspaceProfileSwitcher language={language} />
              <VsCodeSyncStatus language={language} />
              <AdminAccessGate language={language} />
            </section>
          </div>
        </>
      ) : null}

      <Content
        id="brain-main-content"
        className="brain-shell-content"
        tabIndex={-1}
        {...(!contentOwnsMain ? { "aria-label": t(language, "brain.title") } : {})}
      >
        {children}
      </Content>

      <nav className="brain-mobile-nav" aria-label={t(language, "shell.menu.nav")}>
        {primaryRoutes.map((item) => {
          const Icon = item.icon;
          return (
            <a
              key={item.id}
              className={`brain-mobile-nav-item${item.id === active ? " is-active" : ""}`}
              href={`#/${item.path}`}
              aria-current={item.id === active ? "page" : undefined}
            >
              {Icon && <Icon className="nav-icon" aria-hidden="true" />}
              <span>{t(language, item.labelKey)}</span>
            </a>
          );
        })}
        <button
          ref={mobileMenuButtonRef}
          type="button"
          className={`brain-mobile-nav-item brain-mobile-menu-button${
            menuOpen || active === "library" || active === "system" ? " is-active" : ""
          }`}
          aria-expanded={menuOpen}
          aria-controls="brain-more-popover"
          aria-haspopup="dialog"
          aria-label={t(language, menuOpen ? "shell.menu.close" : "shell.menu.open")}
          onClick={(event) => toggleMenu(event.currentTarget)}
        >
          <Ellipsis className="nav-icon" aria-hidden="true" />
          <span>{t(language, "shell.menu.title")}</span>
        </button>
      </nav>
    </div>
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
