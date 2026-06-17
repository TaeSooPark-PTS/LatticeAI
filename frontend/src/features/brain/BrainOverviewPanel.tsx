import * as React from "react";
import { t } from "@/i18n";
import { useAppStore } from "@/store/appStore";
import type { BrainDepth, BrainReadiness, KnowledgeConcept, MemoryFragment } from "./types";

export function BrainOverviewPanel({
  memories,
  concepts,
  readiness,
  onOpenDepth,
}: {
  memories: MemoryFragment[];
  concepts: KnowledgeConcept[];
  readiness: BrainReadiness;
  onOpenDepth: (depth: BrainDepth) => void;
}) {
  const language = useAppStore((state) => state.language);
  const recent = memories.slice(0, 3);
  const older = memories.slice(3, 6);
  const topics = concepts.slice(0, 4);

  return (
    <section className="brain-overview-panel" aria-label={t(language, "brain.aria.overview")}>
      <div className="brain-overview-head">
        <div>
          <span>{t(language, "brain.overview.kicker")}</span>
          <strong>{t(language, "brain.overview.title")}</strong>
        </div>
        <button type="button" onClick={() => onOpenDepth(5)}>{t(language, "brain.overview.graph")}</button>
      </div>
      <div className="brain-overview-grid">
        <BrainOverviewColumn
          title={t(language, "brain.overview.recent")}
          empty={t(language, "brain.overview.recentEmpty")}
          items={recent.map((memory) => memory.title)}
          onOpen={() => onOpenDepth(2)}
        />
        <BrainOverviewColumn
          title={t(language, "brain.overview.older")}
          empty={t(language, "brain.overview.olderEmpty")}
          items={older.map((memory) => memory.title)}
          onOpen={() => onOpenDepth(2)}
        />
        <BrainOverviewColumn
          title={t(language, "brain.overview.topics")}
          empty={t(language, "brain.overview.topicsEmpty")}
          items={topics.map((concept) => concept.label)}
          onOpen={() => onOpenDepth(3)}
        />
      </div>
      <div className="brain-readiness-strip" data-state={readiness.state}>
        <div>
          <span>{t(language, "brain.readiness.kicker")}</span>
          <strong>{t(language, readiness.titleKey)}</strong>
        </div>
        <div className="brain-readiness-meter" aria-label={t(language, "brain.readiness.aria")}>
          <i style={{ width: `${readiness.score}%` }} />
        </div>
        <button type="button" onClick={() => onOpenDepth(readiness.depth)}>
          {t(language, readiness.actionKey)}
        </button>
      </div>
    </section>
  );
}

function BrainOverviewColumn({
  title,
  empty,
  items,
  onOpen,
}: {
  title: string;
  empty: string;
  items: string[];
  onOpen: () => void;
}) {
  return (
    <button type="button" className="brain-overview-column" onClick={onOpen}>
      <span>{title}</span>
      {items.length ? (
        items.slice(0, 3).map((item) => <strong key={item}>{item}</strong>)
      ) : (
        <em>{empty}</em>
      )}
    </button>
  );
}
