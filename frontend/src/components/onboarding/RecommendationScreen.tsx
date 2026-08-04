import { Button } from "@/components/ui/button";
import { t, type Language } from "@/i18n";
import { useAppStore } from "@/store/appStore";
import {
  type AnalysisStatus,
  type AnalysisUnavailableReason,
  type RecommendedModel,
  asRecord,
  type FlowAnalysis,
} from "./recommendationModel";
import { ArrowRight, Gauge, RefreshCw, Star, TriangleAlert, Zap } from "lucide-react";

export function RecommendationScreen({
  status,
  reason,
  recommendations,
  analysis,
  onBack,
  onRetry,
  onSkipModel,
  onSelect,
}: {
  status: AnalysisStatus;
  reason: AnalysisUnavailableReason | null;
  recommendations: RecommendedModel[];
  analysis: FlowAnalysis | null;
  onBack: () => void;
  onRetry: () => void;
  onSkipModel: () => void;
  onSelect: (model: RecommendedModel) => void;
}) {
  const language = useAppStore((state) => state.language);

  if (status === "loading") {
    return (
      <div>
        <div className="ritual-title">{t(language, "flow.recommend.title")}</div>
        <div className="ritual-subtitle">{t(language, "flow.recommend.body")}</div>
        <div className="ritual-scan-banner is-loading" role="status">
          <span className="ritual-scan-dot pulsing" />
          <span>{t(language, "flow.recommend.loading")}</span>
        </div>
        <div className="ritual-action-row">
          <Button variant="ghost" onClick={onBack}>{t(language, "flow.recommend.back")}</Button>
          <Button variant="outline" onClick={onSkipModel}>{t(language, "flow.recommend.skip")}</Button>
        </div>
      </div>
    );
  }

  if (status === "unavailable") {
    const detailKey = reason === "no_supported_model"
      ? "flow.recommend.unavailable.empty"
      : "flow.recommend.unavailable.probe";
    return (
      <div>
        <div className="ritual-title">{t(language, "flow.recommend.title")}</div>
        <section
          className="ritual-card ritual-error-card ritual-unavailable-card"
          role="alert"
          aria-label={t(language, "flow.recommend.unavailable.aria")}
        >
          <div className="ritual-inline-row">
            <TriangleAlert className="ritual-core-icon" aria-hidden="true" />
            <div>
              <div className="ritual-strong-text">{t(language, "flow.recommend.unavailable.title")}</div>
              <div className="ritual-muted-text">{t(language, detailKey)}</div>
            </div>
          </div>
        </section>
        <div className="ritual-button-row ritual-button-row-primary">
          <button type="button" className="ritual-full-button ritual-full-button-primary" onClick={onRetry}>
            <RefreshCw size={16} /> {t(language, "flow.recommend.unavailable.retry")}
          </button>
          <button type="button" className="ritual-secondary-button" onClick={onSkipModel}>
            {t(language, "flow.recommend.skip")}
          </button>
        </div>
        <div className="ritual-action-row">
          <Button variant="ghost" onClick={onBack}>{t(language, "flow.recommend.back")}</Button>
          <div className="ritual-muted-hint">{t(language, "flow.recommend.unavailable.hint")}</div>
        </div>
      </div>
    );
  }

  const items = recommendations;

  function renderEnvironmentCheck(analysis: FlowAnalysis | null, language: Language) {
    if (!analysis) {
      return (
        <div className="ritual-scan-banner is-loading">
          <span className="ritual-scan-dot pulsing" />
          <span>{t(language, "flow.analysis.checking")}</span>
        </div>
      );
    }

    const recs = asRecord(analysis.recommendations?.recommendations);
    const setupEnv = asRecord(analysis.setup?.environment);
    const recProfile = asRecord(analysis.recommendations?.profile);
    const profile = { ...setupEnv, ...recProfile };
    const ramGb = Number(recs.ram_gb || Number(profile.ram_mb || 0) / 1024 || 0);
    const appleSilicon = Boolean(recs.apple_silicon || String(profile.arch || "").includes("arm"));

    if (appleSilicon) {
      return (
        <div className="ritual-scan-banner is-success">
          <span className="ritual-scan-dot is-success" />
          <span>
            {t(language, "flow.recommend.environment.apple", { ram: Math.round(ramGb) })}
          </span>
        </div>
      );
    } else {
      return (
        <div className="ritual-scan-banner is-warning">
          <span className="ritual-scan-dot is-warning" />
          <span>
            {t(language, "flow.recommend.environment.standard", { ram: Math.round(ramGb) })}
          </span>
        </div>
      );
    }
  }

  return (
    <div className="ritual-recommend">
      <header>
        <h1 className="ritual-title">{t(language, "flow.recommend.title")}</h1>
        <p className="ritual-subtitle">{t(language, "flow.recommend.body")}</p>
        <div className="ritual-env-line">
          {renderEnvironmentCheck(analysis, language)}
        </div>
      </header>

      {/* Hero card is the main star of the screen */}
      {items[0] ? (
        <section className="ritual-primary-hero-card" aria-labelledby="recommend-primary-name">
          <div className="ritual-hero-topline">
            <span className="ritual-hero-rank">
              <Star size={16} aria-hidden="true" />
              {rankLabel(items[0].role, 0, language)}
            </span>
            <span className="ritual-hero-size">{items[0].size || t(language, "flow.recommend.sizeReady")}</span>
          </div>

          <h2 id="recommend-primary-name" className="ritual-hero-name">{items[0].shortName}</h2>
          <p className="ritual-hero-reason">
            {items[0].reason}
            {comparisonLabel(items[0].role, language) ? (
              <span className="ritual-model-comparison"> {comparisonLabel(items[0].role, language)}</span>
            ) : null}
          </p>

          <p className="ritual-hero-time">
            <span className="ritual-time-estimate">{timeEstimate(items[0], language)}</span>
            <span className="ritual-time-note">{t(language, "flow.recommend.timeNote")}</span>
          </p>

          {items[0].supported ? (
            <>
              <Button onClick={() => onSelect(items[0])} className="ritual-primary-model-button">
                {t(language, "flow.recommend.primary")} <ArrowRight size={18} aria-hidden="true" />
              </Button>
              <p className="ritual-hero-next">
                {primaryNote(items[0], language)} · {t(language, items[0].downloadRequired ? "flow.recommend.nextHint" : "flow.recommend.nextHint.ready")}
              </p>
            </>
          ) : (
            <p className="ritual-model-warning">{t(language, "flow.recommend.unsupported")}</p>
          )}
        </section>
      ) : null}

      {/* Other choices are collapsed in <details> by default */}
      {items.length > 1 ? (
        <details className="ritual-alternatives-details">
          <summary id="recommend-alternatives" className="ritual-alternatives-title cursor-pointer">
            <h2 className="inline text-sm font-semibold">{t(language, "flow.recommend.alternatives")}</h2>
          </summary>
          <div className="ritual-alt-list flex flex-col gap-3 pt-3">
            {items.slice(1, 3).map((model, index) => {
              const Icon = model.role === "faster" ? Zap : Gauge;
              return (
                <button
                  key={`${model.role}-${model.id}`}
                  type="button"
                  className="ritual-model-card is-compact"
                  onClick={() => model.supported && onSelect(model)}
                  disabled={!model.supported}
                >
                  <span className="ritual-model-heading">
                    <Icon size={15} aria-hidden="true" />
                    <span className="role">{rankLabel(model.role, index + 1, language)}</span>
                    <span className="ritual-alt-size">{model.size || t(language, "flow.recommend.sizeReady")}</span>
                  </span>
                  <span className="name">{model.shortName}</span>
                  <span className="reason">{model.reason}</span>
                  <span className="ritual-alt-footer">
                    <span className="ritual-time-estimate">{timeEstimate(model, language)}</span>
                    {model.supported ? (
                      <span className="ritual-model-choose">
                        {t(language, "flow.recommend.choose")} <ArrowRight size={14} aria-hidden="true" />
                      </span>
                    ) : (
                      <span className="ritual-model-warning">{t(language, "flow.recommend.unsupported")}</span>
                    )}
                  </span>
                </button>
              );
            })}
          </div>
        </details>
      ) : null}

      <footer className="ritual-action-row is-split">
        <Button variant="ghost" onClick={onBack}>{t(language, "flow.recommend.back")}</Button>
        <span className="ritual-action-tail">
          <span className="ritual-muted-hint">{t(language, "flow.recommend.hint")}</span>
          <Button variant="outline" onClick={onSkipModel}>{t(language, "flow.recommend.skip")}</Button>
        </span>
      </footer>
    </div>
  );
}

function formatMinutes(minutes: number, language: Language) {
  return t(language, "flow.recommend.minutes", { count: minutes });
}

function timeEstimate(model: RecommendedModel, language: Language) {
  const response = model.estimatedFirstResponseSeconds;
  if (!model.downloadRequired || model.estimatedDownloadMinutes === 0) {
    return t(language, "flow.recommend.timeEstimate.ready", { response });
  }
  if (model.estimatedDownloadMinutes === null) {
    return t(language, "flow.recommend.timeEstimate.unknown", { response });
  }
  return t(language, "flow.recommend.timeEstimate", {
    download: formatMinutes(model.estimatedDownloadMinutes, language),
    response,
  });
}

function primaryNote(model: RecommendedModel, language: Language) {
  if (!model.downloadRequired || model.estimatedDownloadMinutes === 0) {
    return t(language, "flow.recommend.primaryNote.ready");
  }
  if (model.estimatedDownloadMinutes === null) {
    return t(language, "flow.recommend.primaryNote.unknown");
  }
  return t(language, "flow.recommend.primaryNote", {
    time: formatMinutes(model.estimatedDownloadMinutes, language),
  });
}

function comparisonLabel(role: RecommendedModel["role"], language: Language) {
  if (role === "faster") return t(language, "flow.recommend.comparison.faster");
  if (role === "advanced") return t(language, "flow.recommend.comparison.advanced");
  return "";
}

function rankLabel(role: RecommendedModel["role"], index: number, language: Language) {
  if (role === "best") return t(language, "flow.recommend.rank.best");
  if (role === "faster") return t(language, "flow.recommend.rank.faster");
  if (role === "advanced") return t(language, "flow.recommend.rank.advanced");
  return t(language, "flow.recommend.rank.choice", { index: index + 1 });
}
