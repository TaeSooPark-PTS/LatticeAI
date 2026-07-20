import * as React from "react";
import { Check, Sparkles, X } from "lucide-react";

import { t, type Language } from "@/i18n";
import {
  countFirstFiveDone,
  dismissFirstFive,
  FIRST_FIVE_STEPS,
  markFirstFiveStepDone,
  readFirstFiveState,
  shouldShowFirstFive,
  type FirstFiveStep,
} from "./firstFive";

const STEP_COPY: Record<FirstFiveStep, { labelKey: string; detailKey: string }> = {
  ask: { labelKey: "brain.firstFive.step.ask", detailKey: "brain.firstFive.step.ask.detail" },
  add: { labelKey: "brain.firstFive.step.add", detailKey: "brain.firstFive.step.add.detail" },
  learned: { labelKey: "brain.firstFive.step.learned", detailKey: "brain.firstFive.step.learned.detail" },
};

// The guided "first 5 minutes" card for brand-new users on the empty home.
// Every action is wired to a real product surface (composer, ingestion dock,
// insights shelf) — no demo data. Progress lives in lattice.firstFive.* and
// the card disappears forever once completed or dismissed.
export function FirstFiveCard({
  language,
  autoDone,
  onStep,
}: {
  language: Language;
  // Real product signals (a past conversation, a live ingestion) complete
  // steps automatically so the checklist never nags about work already done.
  autoDone: Partial<Record<FirstFiveStep, boolean>>;
  onStep: (step: FirstFiveStep) => void;
}) {
  const [state, setState] = React.useState(readFirstFiveState);

  React.useEffect(() => {
    for (const step of FIRST_FIVE_STEPS) {
      if (autoDone[step] && !state.done[step]) setState(markFirstFiveStepDone(step));
    }
  }, [autoDone, state.done]);

  if (!shouldShowFirstFive(state)) return null;

  const doneCount = countFirstFiveDone(state);

  return (
    <section className="brain-first-five" aria-label={t(language, "brain.firstFive.aria")} data-testid="brain-first-five">
      <header className="brain-first-five-head">
        <span className="brain-first-five-title">
          <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
          {t(language, "brain.firstFive.title")}
        </span>
        <span className="brain-first-five-progress">
          {t(language, "brain.firstFive.progress", { done: doneCount, total: FIRST_FIVE_STEPS.length })}
        </span>
        <button
          type="button"
          className="brain-first-five-dismiss"
          aria-label={t(language, "brain.firstFive.dismiss")}
          onClick={() => setState(dismissFirstFive())}
        >
          <X className="h-3.5 w-3.5" aria-hidden="true" />
        </button>
      </header>
      <p className="brain-first-five-subtitle">{t(language, "brain.firstFive.subtitle")}</p>
      <div className="brain-first-five-steps">
        {FIRST_FIVE_STEPS.map((step, index) => {
          const done = state.done[step];
          return (
            <button
              key={step}
              type="button"
              className={`brain-first-five-step ${done ? "is-done" : ""}`}
              disabled={done}
              onClick={() => {
                onStep(step);
                // "add" is only marked done when something is really ingested
                // (via autoDone) — clicking just guides the user to the dock.
                if (step !== "add") setState(markFirstFiveStepDone(step));
              }}
            >
              <span className="brain-first-five-step-index" aria-hidden="true">
                {done ? <Check className="h-3.5 w-3.5" /> : index + 1}
              </span>
              <span className="brain-first-five-step-copy">
                <strong>{t(language, STEP_COPY[step].labelKey)}</strong>
                <span>{t(language, STEP_COPY[step].detailKey)}</span>
              </span>
              {done ? <span className="brain-first-five-step-done">{t(language, "brain.firstFive.step.done")}</span> : null}
            </button>
          );
        })}
      </div>
    </section>
  );
}
