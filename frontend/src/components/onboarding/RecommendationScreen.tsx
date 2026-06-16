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
          <Button onClick={() => onSelect(items[0])} className="ritual-primary-model-button">
            {t(language, "flow.recommend.primary")}
          </Button>
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
            <div className="reason">{model.reason} · {model.size || t(language, "flow.recommend.sizeReady")}</div>
            {!model.supported && <div className="ritual-model-warning">{t(language, "flow.recommend.unsupported")}</div>}
          </button>
        ))}
      </div>

      <div className="ritual-action-row">
        <Button variant="ghost" onClick={onBack}>{t(language, "flow.recommend.back")}</Button>
        <Button variant="outline" onClick={onSkipModel}>{t(language, "flow.recommend.skip")}</Button>
        <div className="ritual-muted-hint">{t(language, "flow.recommend.hint")}</div>
      </div>
    </div>
  );
}

function rankLabel(role: RecommendedModel["role"], index: number, language: Language) {
  if (role === "best") return t(language, "flow.recommend.rank.best");
  if (role === "faster") return t(language, "flow.recommend.rank.faster");
  if (role === "advanced") return t(language, "flow.recommend.rank.advanced");
  return t(language, "flow.recommend.rank.choice", { index: index + 1 });
}
