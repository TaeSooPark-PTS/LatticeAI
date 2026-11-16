import * as React from "react";
// Route-scoped copy: importing the namespace registers it into the shared
// table and keeps it inside this lazy chunk instead of the entry bundle.
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

/**
 * One way in, three shapes of material.
 *
 * These used to be three of four page-level tabs, sitting beside "처리 과정" as
 * if choosing a *file* and inspecting the *indexer* were the same kind of
 * decision. A newcomer arriving to add something had to first work out which of
 * four equal-looking tabs held the thing they wanted, and connecting a folder —
 * the highest-value action on the screen — was hidden two tabs deep.
 *
 * Now the page has one primary station at the top that owns every way of adding
 * material, and everything that reports on material already added drops to a
 * quieter row beneath it. The deep links (`#/capture`, `#/my-computer`,
 * `#/capture-browser`) still land on their own method, and `#/pipeline` still
 * works — the status it named is simply always on screen now.
 */
export function CapturePage({ initialTab }: { initialTab?: string }) {
  const language = useAppStore((state) => state.language);
  const [method, setMethod] = React.useState<CaptureMethod>(() => normalizeCaptureMethod(initialTab));
  React.useEffect(() => {
    setMethod(normalizeCaptureMethod(initialTab));
  }, [initialTab]);
  const selectMethod = (next: CaptureMethod) => {
    setMethod(next);
    // Every CaptureMethod has exactly one entry in captureMethods, so this
    // lookup cannot miss; the cast records that invariant instead of a dead
    // fallback branch.
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

      {/* 1순위 — the single station every way in now belongs to. */}
      <section className="capture-station" aria-label={t(language, "capture.station.aria")}>
        <div className="capture-station-head">
          <h2>{t(language, "capture.station.title")}</h2>
          <p>{t(language, "capture.station.detail")}</p>
        </div>
        {/* Toggle buttons, not tabs: these pick the shape of the thing you are
            adding inside one action, rather than switching to another screen. */}
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

      {/* 2순위 — what happened to everything already added. Two columns, both
          quieter than the station: progress on the left, the material itself on
          the right. */}
      <div className="capture-secondary">
        <PipelinePanel />
        <RecentCapturePanel />
      </div>
    </div>
  );
}
