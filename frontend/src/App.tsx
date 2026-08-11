import * as React from "react";
import { type BrainState } from "@/components/LivingBrain";
import { readProductFlowComplete } from "@/components/productFlowState";
import { useAppStore } from "@/store/appStore";
import { parseHash, productShellRoutes } from "@/routes";
import { WorkspaceProfileSwitcher } from "@/components/WorkspaceProfileSwitcher";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { AdminAccessGate } from "@/components/AdminAccessGate";
import { t, type Language } from "@/i18n";
import { latticeApi } from "@/api/client";
import { useQuery } from "@tanstack/react-query";
import { Brain, Ellipsis, Moon, Sun, X } from "lucide-react";
import { navigateHash } from "@/features/brain/navigation";
import { clamp } from "@/lib/utils";
import { CoreServiceUnavailableBanner } from "@/components/CoreServiceUnavailableBanner";
import { CommandPaletteHost } from "@/features/command/CommandPaletteHost";

// Heavy first-run onboarding and the conversation home each carry large subtrees
// (setup flow, chat panels, markdown, memory rings). They are code-split so the
// initial shell chunk stays small; the natural gates (onboarding vs. returning
// user) mean only one is ever fetched on first paint.
const ProductFlow = React.lazy(() => import("@/components/ProductFlow").then((module) => ({ default: module.ProductFlow })));
const BrainHome = React.lazy(() => import("@/features/brain/BrainHome").then((module) => ({ default: module.BrainHome })));

const ActPage = React.lazy(() => import("@/pages/Act").then((module) => ({ default: module.ActPage })));
const BrainPage = React.lazy(() => import("@/pages/Brain").then((module) => ({ default: module.BrainPage })));
const CapturePage = React.lazy(() => import("@/pages/Capture").then((module) => ({ default: module.CapturePage })));
const ChroniclePage = React.lazy(() => import("@/pages/Chronicle").then((module) => ({ default: module.ChroniclePage })));
const LibraryPage = React.lazy(() => import("@/pages/Library").then((module) => ({ default: module.LibraryPage })));
const SystemPage = React.lazy(() => import("@/pages/System").then((module) => ({ default: module.SystemPage })));
// The admin console is a rare, separate surface and carries the whole
// workspace copy namespace. Splitting it keeps both off first paint.
const AdminConsole = React.lazy(() => import("@/features/admin/AdminConsole").then((module) => ({ default: module.AdminConsole })));

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

  if (!flowComplete) {
    return (
      <React.Suspense fallback={<PageLoader language={language} />}>
        <ProductFlow onComplete={() => setFlowComplete(true)} />
      </React.Suspense>
    );
  }

  return (
    <div className="brain-space">
      <div className="brain-field" />
      <CoreServiceUnavailableBanner />
      <CommandPaletteHost language={language} />
      {rawRoute.startsWith("/admin") ? (
        <React.Suspense fallback={<PageLoader language={language} />}>
          <AdminConsole onBack={() => navigateHash("/brain")} />
        </React.Suspense>
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
      ) : parsed.primary === "chronicle" ? (
        <BrainShell active={parsed.primary}>
          <React.Suspense fallback={<PageLoader language={language} />}>
            <ChroniclePage />
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
          <React.Suspense fallback={<PageLoader language={language} />}>
            <BrainHome brainState={brainState} intensity={intensity} onBrainChange={setBrain} />
          </React.Suspense>
        </BrainShell>
      )}
    </div>
  );
}

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Focusable descendants that are actually rendered.
 *
 * The menu holds a copy of the management links that CSS hides once the topbar
 * shows them. Those anchors stay in the DOM, so a plain querySelectorAll would
 * report focus targets the browser will refuse to focus and Tab will skip —
 * putting the focus trap's boundaries in the wrong place.
 */
function focusablesIn(root: HTMLElement | null): HTMLElement[] {
  return Array.from(root?.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR) ?? []).filter(
    (element) => element.offsetWidth > 0 || element.offsetHeight > 0 || element.getClientRects().length > 0,
  );
}

/** Exported for tests: focus a target that may already have gone away. */
export function restoreFocusTo(element: HTMLElement | null) {
  element?.focus();
}

/** Exported for tests: whether a pointer-down landed on the menu or one of its triggers. */
export function pointerDownHitsMenu(
  target: Node,
  panel: HTMLElement | null,
  desktopButton: HTMLElement | null,
  mobileButton: HTMLElement | null,
) {
  return Boolean(
    panel?.contains(target) || desktopButton?.contains(target) || mobileButton?.contains(target),
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
  const [menuOpen, setMenuOpen] = React.useState(false);
  const menuPanelRef = React.useRef<HTMLDivElement>(null);
  const desktopMenuButtonRef = React.useRef<HTMLButtonElement>(null);
  const mobileMenuButtonRef = React.useRef<HTMLButtonElement>(null);
  const returnFocusRef = React.useRef<HTMLButtonElement | null>(null);

  const closeMenu = React.useCallback((restoreFocus = true) => {
    setMenuOpen(false);
    if (restoreFocus) {
      window.requestAnimationFrame(() => restoreFocusTo(returnFocusRef.current));
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
        const focusable = focusablesIn(menuPanelRef.current);
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
        pointerDownHitsMenu(
          target,
          menuPanelRef.current,
          desktopMenuButtonRef.current,
          mobileMenuButtonRef.current,
        )
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
      // The navigation section is hidden once the topbar carries those links,
      // and focus() on a display:none element silently does nothing — which
      // would leave the open dialog with focus still on the trigger. Take the
      // first item that is actually rendered.
      const rendered = focusablesIn(menuPanelRef.current);
      const firstNavigationItem = rendered.find((element) => element.closest(".brain-more-nav"));
      (firstNavigationItem || rendered[0])?.focus();
    });
  }, [menuOpen]);

  // Four everyday destinations stay in the primary nav — 대화 · 자료 · 기억 ·
  // 연대기, in the order `productShellRoutes` lists them; the three management
  // destinations are one list rendered twice — as topbar quick links once the
  // row is wide enough for them, and inside the menu below that. Building both
  // from a single array is what stops the two copies from drifting apart;
  // shell.css owns which one shows, from a single breakpoint, so they can never
  // both appear or both vanish. That breakpoint moved out with the fourth
  // everyday link: the topbar needs ~90px more before both rows fit.
  const primaryRoutes = productShellRoutes.filter((item) => (
    item.id === "brain" || item.id === "capture" || item.id === "memory" || item.id === "chronicle"
  ));
  // Ordered here rather than inherited from the route table: what needs a
  // decision comes before what is merely configurable.
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

        <div className="brain-shell-actions">
          {/* Management destinations, promoted out of the menu so nothing
              everyday costs an extra tap. A second landmark needs its own name
              or screen readers announce two indistinguishable navigations.
              Visibility is owned by shell.css, which hides this copy and the
              one in the menu on opposite sides of a single breakpoint — hence
              no `flex` utility here, which would fight that rule. */}
          <nav
            className="brain-utility-quick-nav"
            aria-label={t(language, "shell.nav.utility")}
          >
            {secondaryRoutes.map((item) => {
              const Icon = item.icon;
              return (
                <a
                  key={item.id}
                  className={`brain-utility-link text-xs flex items-center gap-1.5 px-2.5 py-1.5 rounded-md transition text-muted-foreground hover:text-foreground hover:bg-muted/60${item.id === active ? " is-active font-medium text-foreground bg-muted/80" : ""}`}
                  href={`#/${item.path}`}
                  aria-current={item.id === active ? "page" : undefined}
                >
                  <Icon className="h-3.5 w-3.5" aria-hidden="true" />
                  <span>{t(language, item.labelKey)}</span>
                </a>
              );
            })}
          </nav>
          {/* Language belongs beside appearance */}
          <LanguageSwitcher compact />
          {/* Appearance toggle */}
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
              {/* Named for what it holds, not for what the menu used to hold.
                  This section carries the three management links only, and it
                  is open at exactly the widths where the primary nav — desktop
                  above, bottom bar below — is also on screen. Both were called
                  "화면 이동", so a landmark list showed two navigations with the
                  same name and no way to tell them apart. It shares its name
                  with its topbar twin instead, which is never visible at the
                  same time. */}
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
