import * as React from "react";
import "@/i18n/workspace";
import { Upload } from "lucide-react";
import { t } from "@/i18n";
import { useAppStore } from "@/store/appStore";
import { navigateHash } from "@/features/brain/navigation";
import { FileIntake, FolderIntake, WebIntake } from "@/features/capture/IntakePanels";
import { PipelinePanel, RecentCapturePanel } from "@/features/capture/CaptureStatusPanels";
import {
  browserFolderNameFromFiles,
  type CaptureMethod,
  captureMethods,
  hasDesktopFolderPicker,
  normalizeCaptureMethod,
  readJourney,
  uploadResultDetail,
} from "@/features/capture/captureDomain";

export { browserFolderNameFromFiles, hasDesktopFolderPicker, readJourney, uploadResultDetail };

const ImportPanel = React.lazy(() => import("@/features/capture/ImportPanel"));

export function CapturePage({ initialTab }: { initialTab?: string }) {
  const language = useAppStore((state) => state.language);
  const [method, setMethod] = React.useState<CaptureMethod>(() => normalizeCaptureMethod(initialTab));
  React.useEffect(() => {
    setMethod(normalizeCaptureMethod(initialTab));
  }, [initialTab]);
  const selectMethod = (next: CaptureMethod) => {
    setMethod(next);
    const entry = captureMethods.find((item) => item.id === next) as (typeof captureMethods)[number];
    navigateHash("/" + entry.path);
  };
  return (
    <div className="product-page capture-page space-y-5">
      <header className="page-hero">
        <div className="page-kicker"><Upload className="h-4 w-4" /> {t(language, "capture.kicker")}</div>
        <h1 className="page-title">{t(language, "capture.title")}</h1>
        <p className="page-copy">{t(language, "capture.body")}</p>
      </header>

      <section className="capture-station" aria-label={t(language, "capture.station.aria")}>
        <div className="capture-station-head">
          <h2>{t(language, "capture.station.title")}</h2>
          <p>{t(language, "capture.station.detail")}</p>
        </div>
        <div className="capture-method-switch" role="group" aria-label={t(language, "capture.method.aria")}>
          {captureMethods.map(({ id, labelKey, icon: Icon }) => (
            <button
              key={id}
              type="button"
              aria-pressed={method === id}
              className={method === id ? "is-active" : ""}
              data-testid={`capture-method-${id}`}
              onClick={() => selectMethod(id)}
            >
              <Icon className="h-4 w-4" aria-hidden="true" />
              {t(language, labelKey)}
            </button>
          ))}
        </div>
        <div className="capture-station-body">
          {method === "files" ? <FileIntake /> : null}
          {method === "local" ? <FolderIntake /> : null}
          {method === "browser" ? <WebIntake /> : null}
        </div>
      </section>

      <React.Suspense fallback={<p role="status" className="text-sm text-muted-foreground">{t(language, "ui.loading")}</p>}>
        <ImportPanel />
      </React.Suspense>

      <div className="capture-secondary">
        <PipelinePanel />
        <RecentCapturePanel />
      </div>
    </div>
  );
}
