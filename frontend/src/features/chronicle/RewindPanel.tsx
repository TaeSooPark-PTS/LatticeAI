import * as React from "react";
import { RotateCcw } from "lucide-react";

import type { ChronicleAsOf } from "@/api/client";
import { navigateHash } from "@/features/brain/navigation";
import { t, type Language } from "@/i18n";
import { fmtNumber } from "@/lib/utils";
import { entityTypeLabel } from "./DayStory";

/**
 * The Brain as it stood on the chosen day — the first time `store.as_of()` has
 * ever been visible in the product.
 *
 * Two numbers and a short list, and one sentence saying what the numbers are
 * *not*: `stats.entities` counts every node in that slice, documents included,
 * which is a different measure from the concept-only "개념" lane in the growth
 * curve above. Printing them side by side without that note would make the
 * screen look like it contradicts itself.
 */
export function RewindPanel({
  asOf,
  date,
  loading,
  onReset,
  language,
}: {
  asOf: ChronicleAsOf | null;
  date: string;
  loading: boolean;
  onReset: () => void;
  language: Language;
}) {
  return (
    <section className="chronicle-panel chronicle-rewind" data-testid="chronicle-rewind">
      <header className="chronicle-panel-head">
        <h2 className="chronicle-panel-title">{t(language, "chronicle.rewind.title")}</h2>
        <p className="chronicle-panel-hint">{t(language, "chronicle.rewind.subtitle", { date })}</p>
        <button type="button" className="chronicle-reset" onClick={onReset}>
          <RotateCcw aria-hidden="true" />
          {t(language, "chronicle.rewind.reset")}
        </button>
      </header>

      {loading || !asOf ? (
        <p className="chronicle-note" role="status">{t(language, "chronicle.rewind.loading")}</p>
      ) : (
        <>
          <dl className="chronicle-lane-row chronicle-rewind-stats">
            <div className="chronicle-lane">
              <dt>{t(language, "chronicle.rewind.entities")}</dt>
              <dd data-testid="chronicle-rewind-entities">{fmtNumber(asOf.stats.entities)}</dd>
            </div>
            <div className="chronicle-lane">
              <dt>{t(language, "chronicle.rewind.connections")}</dt>
              <dd>{fmtNumber(asOf.stats.connections)}</dd>
            </div>
          </dl>
          <p className="chronicle-note chronicle-rewind-note">{t(language, "chronicle.rewind.note")}</p>

          <h3 className="chronicle-group-title">{t(language, "chronicle.rewind.top")}</h3>
          {asOf.top_entities.length === 0 ? (
            <p className="chronicle-group-empty">{t(language, "chronicle.rewind.topEmpty")}</p>
          ) : (
            <ul className="chronicle-group-list chronicle-rewind-list">
              {asOf.top_entities.map((entity) => (
                <li key={entity.id}>
                  <button
                    type="button"
                    className="chronicle-item"
                    aria-label={`${entity.label} — ${t(language, "chronicle.open.entity")}`}
                    onClick={() => navigateHash("/knowledge-graph")}
                  >
                    <span className="chronicle-item-title">{entity.label}</span>
                    <span className="chronicle-item-meta">{entityTypeLabel(entity.type, language)}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}
