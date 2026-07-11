import { Button } from "@/components/ui/button";
import { t, type Language } from "@/i18n";
import { useAppStore } from "@/store/appStore";
import { fallbackModel, type RecommendedModel, asRecord, type FlowAnalysis } from "./recommendationModel";
import { ArrowRight, Gauge, Star, Zap } from "lucide-react";

export function RecommendationScreen({
  recommendations,
  analysis,
  onBack,
  onSkipModel,
  onSelect,
}: {
  recommendations: RecommendedModel[];
  analysis: FlowAnalysis | null;
  onBack: () => void;
  onSkipModel: () => void;
  onSelect: (model: RecommendedModel) => void;
}) {
  const language = useAppStore((state) => state.language);
  const items = recommendations.length ? recommendations : [fallbackModel()];

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
    <div>
      <div className="ritual-title">{t(language, "flow.recommend.title")}</div>
      <div className="ritual-subtitle">{t(language, "flow.recommend.body")}</div>
      {renderEnvironmentCheck(analysis, language)}

      <div className="ritual-model-list">
        {items[0]?.supported ? (
          <div className="ritual-primary-cta">
            <Button onClick={() => onSelect(items[0])} className="ritual-primary-model-button">
              {t(language, "flow.recommend.primary")} <ArrowRight size={16} />
            </Button>
            <div className="ritual-time-estimate ritual-primary-note">{primaryNote(items[0], language)}</div>
            <div className="ritual-muted-hint ritual-next-hint">
              {t(language, items[0].downloadRequired ? "flow.recommend.nextHint" : "flow.recommend.nextHint.ready")}
            </div>
          </div>
        ) : null}
        {items.slice(0, 3).map((model, index) => {
          const Icon = model.role === "best" ? Star : model.role === "faster" ? Zap : Gauge;
          return (
            <button
              key={`${model.role}-${model.id}`}
              className="ritual-model-card"
              onClick={() => model.supported && onSelect(model)}
              disabled={!model.supported}
            >
              <div className="ritual-model-heading">
                <Icon size={16} />
                <div className="role">{rankLabel(model.role, index, language)}</div>
              </div>
              <div className="name">{model.shortName}</div>
              <div className="reason">
                {model.reason} · {model.size || t(language, "flow.recommend.sizeReady")}
                {comparisonLabel(model.role, language) ? (
                  <span className="ritual-model-comparison"> {comparisonLabel(model.role, language)}</span>
                ) : null}
              </div>
              <div className="ritual-model-stats">
                <span className="ritual-time-estimate">{timeEstimate(model, language)}</span>
              </div>
              {model.supported ? (
                <span className="ritual-model-choose">{t(language, "flow.recommend.choose")} <ArrowRight size={14} /></span>
              ) : (
                <div className="ritual-model-warning">{t(language, "flow.recommend.unsupported")}</div>
              )}
            </button>
          );
        })}
        <div className="ritual-time-note">{t(language, "flow.recommend.timeNote")}</div>
      </div>

      <div className="ritual-action-row">
        <Button variant="ghost" onClick={onBack}>{t(language, "flow.recommend.back")}</Button>
        <Button variant="outline" onClick={onSkipModel}>{t(language, "flow.recommend.skip")}</Button>
        <div className="ritual-muted-hint">{t(language, "flow.recommend.hint")}</div>
      </div>
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
