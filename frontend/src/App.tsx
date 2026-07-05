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
  return (
    <main className="brain-shell-page" aria-label="Lattice workspace">
      <nav className="brain-shell-nav" aria-label="Brain workspace navigation">
        {productShellRoutes.map((item) => {
          const Icon = item.icon;
          return (
            <button
              key={item.id}
              type="button"
              className={`nav-item ${item.id === active ? "is-active" : ""}`}
              aria-current={item.id === active ? "page" : undefined}
              title={`${t(language, item.labelKey)} — ${item.description}`}
              onClick={() => navigateHash(`/${item.path}`)}
            >
              {Icon && <Icon className="nav-icon" aria-hidden="true" />}
              <span>{t(language, item.labelKey)}</span>
            </button>
          );
        })}
        <div className="brain-shell-switchers" aria-label={t(language, "shell.workspace.label")}>
          <VsCodeSyncStatus language={language} />
          <WorkspaceProfileSwitcher language={language} />
          <AdminAccessGate language={language} />
        </div>
      </nav>
      <section className="brain-shell-content">
        <ExternalConsentStatus language={language} />
        {children}
      </section>
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
