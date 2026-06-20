import { Button } from "@/components/ui/button";
import { t, type Language } from "@/i18n";
import { useAppStore } from "@/store/appStore";
import { fallbackModel, type RecommendedModel } from "./recommendationModel";

export function RecommendationScreen({
  recommendations,
  onBack,
  onSkipModel,
  onSelect,
}: {
  recommendations: RecommendedModel[];
  onBack: () => void;
  onSkipModel: () => void;
  onSelect: (model: RecommendedModel) => void;
}) {
  const language = useAppStore((state) => state.language);
  const items = recommendations.length ? recommendations : [fallbackModel()];
  return (
    <div>
      <div className="ritual-title">{t(language, "flow.recommend.title")}</div>
      <div className="ritual-subtitle">{t(language, "flow.recommend.body")}</div>

      <div className="ritual-model-list">
        {items[0]?.supported ? (
          <div className="ritual-primary-cta">
            <Button onClick={() => onSelect(items[0])} className="ritual-primary-model-button">
              {t(language, "flow.recommend.primary")}
            </Button>
            <div className="ritual-time-estimate ritual-primary-note">{primaryNote(items[0], language)}</div>
            <div className="ritual-muted-hint ritual-next-hint">{t(language, "flow.recommend.nextHint")}</div>
          </div>
        ) : null}
        {items.slice(0, 3).map((model, index) => (
          <button
            key={`${model.role}-${model.id}`}
            className="ritual-model-card"
            onClick={() => model.supported && onSelect(model)}
            disabled={!model.supported}
          >
            <div className="role">{rankLabel(model.role, index, language)}</div>
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
              <span className="ritual-model-choose">{t(language, "flow.recommend.choose")}</span>
            ) : (
              <div className="ritual-model-warning">{t(language, "flow.recommend.unsupported")}</div>
            )}
          </button>
        ))}
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
  if (!model.downloadRequired || model.estimatedDownloadMinutes === 0 || model.estimatedDownloadMinutes === null) {
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
