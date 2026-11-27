import * as React from "react";
import { readProductFlowComplete } from "@/components/productFlowState";
import { useAppStore } from "@/store/appStore";
import { productShellRoutes } from "@/routes";
import { WorkspaceProfileSwitcher } from "@/components/WorkspaceProfileSwitcher";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { AdminAccessGate } from "@/components/AdminAccessGate";
import { t, type Language } from "@/i18n";
import { latticeApi } from "@/api/client";
import { useQuery } from "@tanstack/react-query";
import { Brain, Ellipsis, Moon, Search, Sun, X } from "lucide-react";
import { CoreServiceUnavailableBanner } from "@/components/CoreServiceUnavailableBanner";
import { BrainVitalPill } from "@/components/BrainVitalPill";
import { CommandPaletteHost } from "@/features/command/CommandPaletteHost";
import { LazyRoute } from "@/components/ErrorBoundary";
import { AdminRouteView, PageLoader, resolveAppRoute } from "@/AppRouteView";
import {
  pointerDownHitsMenu,
  restoreFocusTo,
  useAppBootstrap,
  useShellMenu,
} from "@/hooks/useAppBootstrap";

export { pointerDownHitsMenu, restoreFocusTo };

const ProductFlow = React.lazy(() =>
  import("@/components/ProductFlow").then((module) => ({ default: module.ProductFlow })),
);

export default function App() {
  const { language, rawRoute, parsed, brainState, intensity, setBrain } = useAppBootstrap();
  const [flowComplete, setFlowComplete] = React.useState(readProductFlowComplete);

  if (!flowComplete) {
    return (
      <LazyRoute language={language} resetKey="onboarding" fallback={<PageLoader language={language} />}>
        <ProductFlow onComplete={() => setFlowComplete(true)} />
      </LazyRoute>
    );
  }

  const resolved = resolveAppRoute({
    rawRoute,
    parsed,
    brainState,
    intensity,
    onBrainChange: setBrain,
  });

  return (
    <div className="brain-space">
      <div className="brain-field" />
      <CoreServiceUnavailableBanner />
      <CommandPaletteHost language={language} />
      {resolved.kind === "admin" ? (
        <AdminRouteView language={language} rawRoute={rawRoute} />
      ) : (
        <BrainShell active={resolved.active} contentOwnsMain={resolved.contentOwnsMain}>
          <LazyRoute language={language} resetKey={rawRoute} fallback={<PageLoader language={language} />}>
            {resolved.element}
          </LazyRoute>
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
  const theme = useAppStore((state) => state.theme);
  const setTheme = useAppStore((state) => state.setTheme);
  const {
    menuOpen,
    menuPanelRef,
    desktopMenuButtonRef,
    mobileMenuButtonRef,
    closeMenu,
    toggleMenu,
  } = useShellMenu(active);

  const primaryRoutes = productShellRoutes.filter((item) => (
    item.id === "brain" || item.id === "capture" || item.id === "memory" || item.id === "chronicle"
  ));
  const secondaryOrder = ["act", "library", "system"];
  const secondaryRoutes = secondaryOrder.flatMap((id) => (
    productShellRoutes.filter((item) => item.id === id)
  ));
  const secondaryIsActive = secondaryRoutes.some((item) => item.id === active);
  const Content = contentOwnsMain ? "div" : "main";
  const skipLabel = t(language, "shell.skip");

  return (
    <div className="brain-shell-page">
      <a
        className="brain-skip-link"
        href="#brain-main-content"
        onClick={(event) => {
          event.preventDefault();
          restoreFocusTo(document.getElementById("brain-main-content"));
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
        <span className="brain-local-badge" data-testid="brain-local-badge">
          {t(language, "shell.localBadge")}
        </span>
        <BrainVitalPill language={language} />

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
                <Icon className="nav-icon" aria-hidden="true" />
                <span>{t(language, item.labelKey)}</span>
              </a>
            );
          })}
        </nav>

        <button
          type="button"
          className="brain-theme-toggle brain-command-button"
          data-testid="topbar-command-button"
          aria-label={t(language, "command.title")}
          aria-haspopup="dialog"
          title={t(language, "command.title")}
          onClick={() => window.dispatchEvent(new Event("lattice:open-command"))}
        >
          <Search aria-hidden="true" />
        </button>

        <div className="brain-shell-actions">
          <nav
            className="brain-utility-quick-nav"
            aria-label={t(language, "shell.nav.utility")}
          >
            {secondaryRoutes.map((item) => {
              const Icon = item.icon;
              return (
                <a
                  key={item.id}
                  className={`brain-utility-link${item.id === active ? " is-active" : ""}`}
                  href={`#/${item.path}`}
                  title={t(language, item.labelKey)}
                  aria-current={item.id === active ? "page" : undefined}
                >
                  <Icon className="h-3.5 w-3.5" aria-hidden="true" />
                  <span>{t(language, item.labelKey)}</span>
                </a>
              );
            })}
          </nav>
          <LanguageSwitcher compact />
          <button
            type="button"
            className="brain-theme-toggle"
            data-testid="topbar-theme-toggle"
            aria-label={t(language, theme === "dark" ? "shell.theme.toLight" : "shell.theme.toDark")}
            title={t(language, theme === "dark" ? "shell.theme.toLight" : "shell.theme.toDark")}
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          >
            {theme === "dark" ? <Sun aria-hidden="true" /> : <Moon aria-hidden="true" />}
          </button>
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

            <section
              className="brain-more-section brain-more-section-compact-only"
              aria-labelledby="brain-more-navigation-label"
            >
              <span id="brain-more-navigation-label" className="brain-more-section-label">
                {t(language, "shell.menu.manage")}
              </span>
              <nav className="brain-more-nav" aria-label={t(language, "shell.nav.utility")}>
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
                      <Icon className="nav-icon" aria-hidden="true" />
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
              <Icon className="nav-icon" aria-hidden="true" />
              <span>{t(language, item.labelKey)}</span>
            </a>
          );
        })}
        <button
          ref={mobileMenuButtonRef}
          type="button"
          className={`brain-mobile-nav-item brain-mobile-menu-button${
            menuOpen || secondaryIsActive ? " is-active" : ""
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
