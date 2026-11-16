import { CheckCircle2, Cpu, Loader2, PlayCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { t } from "@/i18n";
import { humanizeModelId } from "@/lib/utils";
import { useAppStore } from "@/store/appStore";
import { useActiveModelSwitcher } from "./hooks/useLibraryModels";

/**
 * Which model is running, and how to change it — first, and on every tab.
 *
 * This screen used to open on a hero, then a tab strip, then a six-step setup
 * track, and only somewhere below that did it say which model was actually
 * loaded. The question that brings people here ("is my AI working, and can I
 * use a different one?") was answered by a stat cell in the middle of a
 * catalogue. It is the first block on the page now, and the alternatives it
 * offers are only ones a single click can really switch to: already
 * downloaded, no consent prompt, not the one already running.
 */
export function ActiveModelCard() {
  const language = useAppStore((state) => state.language);
  const {
    activeName,
    alternatives,
    modelId,
    unavailable,
    switching,
    switchError,
    switchTo,
  } = useActiveModelSwitcher(language);

  return (
    <section className="library-active" aria-label={t(language, "library.active.title")} data-testid="library-active-model">
      <div className="library-active-main">
        <span className="library-active-mark" aria-hidden="true">
          {activeName && !unavailable ? <PlayCircle className="h-5 w-5" /> : <Cpu className="h-5 w-5" />}
        </span>
        <div className="library-active-copy">
          <span className="library-active-kicker">{t(language, "library.active.title")}</span>
          {unavailable ? (
            <strong>{t(language, "library.active.unknown")}</strong>
          ) : activeName ? (
            <>
              <strong>{activeName}</strong>
              <small>{t(language, "library.active.ready")}</small>
            </>
          ) : (
            <>
              <strong>{t(language, "library.active.none")}</strong>
              <small>{t(language, "library.active.noneHint")}</small>
            </>
          )}
        </div>
        {activeName && !unavailable ? (
          <Badge variant="success">{t(language, "library.model.loaded")}</Badge>
        ) : null}
      </div>

      {alternatives.length && !unavailable ? (
        <div className="library-active-switch">
          <span className="library-active-switch-label">
            {t(language, "library.active.switchTitle")}
            <small>{t(language, "library.active.switchHint")}</small>
          </span>
          <div className="library-active-switch-options">
            {alternatives.map((model) => {
              const id = modelId(model);
              const name = String(model.name || "") || humanizeModelId(id);
              return (
                <button
                  key={id}
                  type="button"
                  disabled={Boolean(switching)}
                  data-testid={`library-switch-${id}`}
                  onClick={() => void switchTo(model)}
                >
                  {switching === id ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />}
                  {switching === id ? t(language, "library.active.switching") : name}
                </button>
              );
            })}
          </div>
        </div>
      ) : null}

      {switchError ? <p className="library-active-error" role="status">{switchError}</p> : null}
    </section>
  );
}
