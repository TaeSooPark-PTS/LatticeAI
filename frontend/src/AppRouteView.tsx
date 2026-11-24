import * as React from "react";
import { navigateHash } from "@/features/brain/navigation";
import { LazyRoute } from "@/components/ErrorBoundary";
import type { BrainState } from "@/components/LivingBrain";
import { t, type Language } from "@/i18n";
import type { RouteTarget } from "@/routes";

type ParsedRoute = RouteTarget;

const BrainHome = React.lazy(() =>
  import("@/features/brain/BrainHome").then((module) => ({ default: module.BrainHome })),
);
const ActPage = React.lazy(() =>
  import("@/pages/Act").then((module) => ({ default: module.ActPage })),
);
const BrainPage = React.lazy(() =>
  import("@/pages/Brain").then((module) => ({ default: module.BrainPage })),
);
const CapturePage = React.lazy(() =>
  import("@/pages/Capture").then((module) => ({ default: module.CapturePage })),
);
const ChroniclePage = React.lazy(() =>
  import("@/pages/Chronicle").then((module) => ({ default: module.ChroniclePage })),
);
const LibraryPage = React.lazy(() =>
  import("@/pages/Library").then((module) => ({ default: module.LibraryPage })),
);
const SystemPage = React.lazy(() =>
  import("@/pages/System").then((module) => ({ default: module.SystemPage })),
);
const AdminConsole = React.lazy(() =>
  import("@/features/admin/AdminConsole").then((module) => ({ default: module.AdminConsole })),
);

export function PageLoader({ language }: { language: Language }) {
  return (
    <div className="brain-shell-loader" role="status">
      {t(language, "shell.loading")}
    </div>
  );
}

export type ResolvedAppRoute =
  | { kind: "admin" }
  | { kind: "shell"; active: string; contentOwnsMain: boolean; element: React.ReactNode };

export function resolveAppRoute({
  rawRoute,
  parsed,
  brainState,
  intensity,
  onBrainChange,
}: {
  rawRoute: string;
  parsed: ParsedRoute;
  brainState: BrainState;
  intensity: number;
  onBrainChange: (next: BrainState, nextIntensity?: number) => void;
}): ResolvedAppRoute {
  if (rawRoute.startsWith("/admin")) {
    return { kind: "admin" };
  }
  switch (parsed.primary) {
    case "act":
      return { kind: "shell", active: "act", contentOwnsMain: false, element: <ActPage initialTab={parsed.tab} /> };
    case "capture":
      return { kind: "shell", active: "capture", contentOwnsMain: false, element: <CapturePage initialTab={parsed.tab} /> };
    case "chronicle":
      return { kind: "shell", active: "chronicle", contentOwnsMain: false, element: <ChroniclePage /> };
    case "library":
      return { kind: "shell", active: "library", contentOwnsMain: false, element: <LibraryPage initialTab={parsed.tab} /> };
    case "system":
      return { kind: "shell", active: "system", contentOwnsMain: false, element: <SystemPage initialTab={parsed.tab} /> };
    case "memory":
      return { kind: "shell", active: "memory", contentOwnsMain: false, element: <BrainPage initialTab="memory" /> };
    default: {
      if (parsed.tab && parsed.tab !== "conversation") {
        const active =
          parsed.tab === "graph" || parsed.tab === "knowledge" || parsed.tab === "memory"
            ? "memory"
            : "brain";
        return { kind: "shell", active, contentOwnsMain: false, element: <BrainPage initialTab={parsed.tab} /> };
      }
      return {
        kind: "shell",
        active: "brain",
        contentOwnsMain: true,
        element: (
          <BrainHome brainState={brainState} intensity={intensity} onBrainChange={onBrainChange} />
        ),
      };
    }
  }
}

export function AdminRouteView({ language, rawRoute }: { language: Language; rawRoute: string }) {
  return (
    <LazyRoute language={language} resetKey={rawRoute} fallback={<PageLoader language={language} />}>
      <AdminConsole onBack={() => navigateHash("/brain")} />
    </LazyRoute>
  );
}
