import * as React from "react";
import { type BrainState, LivingBrain } from "@/components/LivingBrain";
import { t, type Language } from "@/i18n";
import type { BrainDepth, BrainReadiness, KnowledgeConcept, MemoryFragment } from "./types";

type RingId = "now" | "memories" | "topics" | "graph";

type RingModel = {
  id: RingId;
  depth: BrainDepth;
  labelKey: string;
  emptyKey: string;
  position: "top" | "left" | "bottom" | "right";
  count: number;
  items: string[];
};

/**
 * Concentric memory rings around the living Brain.
 *
 * Each ring is a real layer of the Brain (now memory → durable memories →
 * topics → graph), not decoration: populated rings light up, and every ring
 * label is a keyboard-accessible peek button that previews that layer without
 * leaving the home screen. "Go deeper" hands off to the full depth view.
 */
export function MemoryRings({
  language,
  brainState,
  intensity,
  readiness,
  memories,
  concepts,
  relationshipCount,
  onExploreBrain,
  onOpenDepth,
}: {
  language: Language;
  brainState: BrainState;
  intensity: number;
  readiness: BrainReadiness;
  memories: MemoryFragment[];
  concepts: KnowledgeConcept[];
  relationshipCount: number;
  onExploreBrain: () => void;
  onOpenDepth: (depth: BrainDepth) => void;
}) {
  const [activeRing, setActiveRing] = React.useState<RingId | null>(null);

  const rings = React.useMemo<RingModel[]>(() => {
    const nowFragments = memories.filter((memory) => memory.kind === "Conversation");
    const durableFragments = memories
      .filter((memory) => memory.kind !== "Conversation")
      .sort((left, right) => Number(right.agentGenerated) - Number(left.agentGenerated));
    return [
      {
        id: "now",
        depth: 1,
        labelKey: "brain.rings.1",
        emptyKey: "brain.rings.empty.now",
        position: "top",
        count: nowFragments.length,
        items: nowFragments.slice(0, 3).map((memory) => memory.title),
      },
      {
        id: "memories",
        depth: 2,
        labelKey: "brain.rings.2",
        emptyKey: "brain.rings.empty.memories",
        position: "left",
        count: durableFragments.length,
        items: durableFragments.slice(0, 3).map((memory) => memory.title),
      },
      {
        id: "topics",
        depth: 3,
        labelKey: "brain.rings.3",
        emptyKey: "brain.rings.empty.topics",
        position: "bottom",
        count: concepts.length,
        items: concepts.slice(0, 3).map((concept) => concept.label),
      },
      {
        id: "graph",
        depth: 5,
        labelKey: "brain.rings.5",
        emptyKey: "brain.rings.empty.graph",
        position: "right",
        count: relationshipCount,
        items: [],
      },
    ];
  }, [memories, concepts, relationshipCount]);

  React.useEffect(() => {
    if (activeRing === null) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setActiveRing(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [activeRing]);

  const active = rings.find((ring) => ring.id === activeRing) ?? null;

  return (
    <div className="brain-rings" aria-label={t(language, "brain.rings.aria")}>
      <div className="brain-concentric-container">
        <div className="brain-orbit-field" aria-hidden="true">
          {rings.map((ring, index) => (
            <div
              key={ring.id}
              className={`brain-concentric-ring ring-${index + 1} ${ring.count > 0 ? "is-populated" : "is-dormant"}`}
            />
          ))}
        </div>

        {rings.map((ring) => (
          <button
            key={ring.id}
            type="button"
            className={`ring-label ring-label-${ring.position} ${ring.count > 0 ? "is-populated" : ""} ${activeRing === ring.id ? "is-active" : ""}`}
            aria-expanded={activeRing === ring.id}
            aria-controls="brain-ring-peek"
            aria-label={t(language, "brain.rings.peek.aria", { label: t(language, ring.labelKey), count: ring.count })}
            onClick={() => setActiveRing((current) => (current === ring.id ? null : ring.id))}
          >
            {t(language, ring.labelKey)}
            <em>{ring.count}</em>
          </button>
        ))}

        <div className="brain-center-orb">
          <LivingBrain
            state={brainState}
            intensity={intensity}
            size="large"
            depth={readiness.depth || Math.max(1, Math.floor(readiness.score / 20))}
            showLabel={false}
            className="brain-home-presence"
            onInteract={onExploreBrain}
          />
        </div>
      </div>

      {active ? (
        <section id="brain-ring-peek" className="brain-ring-peek" aria-label={t(language, "brain.rings.peek.title", { label: t(language, active.labelKey) })}>
          <header>
            <strong>{t(language, active.labelKey)}</strong>
            <span>{t(language, "brain.rings.count", { count: active.count })}</span>
            <button type="button" onClick={() => setActiveRing(null)} aria-label={t(language, "brain.rings.close")}>
              ×
            </button>
          </header>
          {active.items.length ? (
            <ul>
              {active.items.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          ) : (
            <p>{t(language, active.count > 0 ? "brain.rings.peek.summary" : active.emptyKey, { count: active.count })}</p>
          )}
          <button type="button" className="brain-ring-peek-deeper" onClick={() => onOpenDepth(active.depth)}>
            {t(language, "brain.rings.deeper")}
          </button>
        </section>
      ) : null}
    </div>
  );
}
