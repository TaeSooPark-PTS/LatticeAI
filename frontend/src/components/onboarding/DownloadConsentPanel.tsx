import { t } from "@/i18n";
import { useAppStore } from "@/store/appStore";
import { type RecommendedModel } from "./recommendationModel";

export function DownloadConsentPanel({ model }: { model: RecommendedModel }) {
  const language = useAppStore((state) => state.language);
  const items = [
    { label: t(language, "flow.consent.size"), value: model.downloadSize || t(language, "flow.consent.sizeUnknown") },
    { label: t(language, "flow.consent.location"), value: model.storageLocation },
    { label: t(language, "flow.consent.external"), value: model.externalHost || t(language, "flow.consent.externalNone") },
  ];

  return (
    <section className="ritual-consent-panel" aria-label={t(language, "flow.consent.title")}>
      <div>
        <strong>{t(language, "flow.consent.title")}</strong>
        <p>{model.downloadRequired ? t(language, "flow.consent.body") : t(language, "flow.consent.ready")}</p>
      </div>
      <dl>
        {items.map((item) => (
          <div key={item.label}>
            <dt>{item.label}</dt>
            <dd>{item.value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
