import { LANGUAGE_LABELS, t, type Language } from "@/i18n";
import { useAppStore } from "@/store/appStore";

export function LanguageSwitcher({ compact = false }: { compact?: boolean }) {
  const language = useAppStore((state) => state.language);
  const setLanguage = useAppStore((state) => state.setLanguage);

  return (
    <div className={compact ? "language-switcher compact" : "language-switcher"} aria-label={t(language, "language.label")}>
      {(["ko", "en"] as Language[]).map((item) => (
        <button
          key={item}
          type="button"
          className={language === item ? "is-active" : ""}
          onClick={() => setLanguage(item)}
          aria-pressed={language === item}
        >
          {LANGUAGE_LABELS[item]}
        </button>
      ))}
    </div>
  );
}
