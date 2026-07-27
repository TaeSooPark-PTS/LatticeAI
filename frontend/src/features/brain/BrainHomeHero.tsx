import * as React from "react";

import { type BrainState, LivingBrain } from "@/components/LivingBrain";
import { t, type Language } from "@/i18n";
import type { BrainReadiness, KnowledgeGraphModel, MemoryFragment } from "./types";

/**
 * The home hero: the Brain itself, a greeting, and one plain sentence about
 * what is remembered. Everything graph-shaped now lives behind the Brain — you
 * click the organism to open the knowledge graph — so the first screen stays a
 * single readable thought instead of a dashboard.
 */
export const BrainHomeHero = React.memo(function BrainHomeHero({
  language,
  brainState,
  intensity,
  readiness,
  memories,
  graph,
  onExploreBrain,
}: {
  language: Language;
  brainState: BrainState;
  intensity: number;
  readiness: BrainReadiness;
  memories: MemoryFragment[];
  graph: KnowledgeGraphModel;
  onExploreBrain: () => void;
}) {
  const memoryCount = Math.max(readiness.signals.memoryCount, memories.length);
  const conceptCount = Math.max(readiness.signals.conceptCount, graph.nodes.length);
  const empty = memoryCount === 0 && conceptCount === 0;

  return (
    <section className="brain-hero" data-testid="brain-knowledge-flow" aria-labelledby="brain-home-title">
      {/* LivingBrain already renders its own button, so this is a plain wrapper
          that only sizes it — nesting a button inside a button is invalid. */}
      <div className="brain-hero-organism">
        <LivingBrain
          state={brainState}
          intensity={intensity}
          size="large"
          depth={readiness.depth}
          showLabel={false}
          onInteract={onExploreBrain}
        />
      </div>

      <h1 id="brain-home-title">{t(language, "brain.firstScreen.title")}</h1>
      <p className="brain-hero-line">
        {empty
          ? t(language, "brain.hero.empty")
          : t(language, "brain.hero.summary", { memories: memoryCount, concepts: conceptCount })}
        <span className="brain-hero-hint">{t(language, "brain.hero.hint")}</span>
      </p>
    </section>
  );
});
