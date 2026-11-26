import * as React from "react";
import { Map } from "lucide-react";

import { type BrainState, LivingBrain } from "@/components/LivingBrain";
import { computeBrainGrowth } from "@/features/brain3d/brainGrowth";
import { t, type Language } from "@/i18n";
import { useAppStore } from "@/store/appStore";
import type { BrainReadiness, KnowledgeGraphModel, MemoryFragment } from "./types";

const Brain3DCanvas = React.lazy(() =>
  import("@/features/brain3d/Brain3DCanvas").then((module) => ({ default: module.Brain3DCanvas })),
);

export const BrainHomeHero = React.memo(function BrainHomeHero({
  language,
  brainState,
  intensity,
  readiness,
  memories,
  graph,
  relationshipCount,
  onExploreBrain,
  trailing,
}: {
  language: Language;
  brainState: BrainState;
  intensity: number;
  readiness: BrainReadiness;
  memories: MemoryFragment[];
  graph: KnowledgeGraphModel;
  relationshipCount: number;
  onExploreBrain: () => void;
  trailing?: React.ReactNode;
}) {
  const graphViewMode = useAppStore((state) => state.graphViewMode);
  const showSuperseded = useAppStore((state) => state.showSuperseded);
  const activeFiringTrace = useAppStore((state) => state.activeFiringTrace);
  const memoryCount = Math.max(readiness.signals.memoryCount, memories.length);
  const conceptCount = Math.max(readiness.signals.conceptCount, graph.nodes.length);
  const growth = React.useMemo(
    () => computeBrainGrowth(Math.max(conceptCount, graph.nodes.length), Math.max(relationshipCount, graph.edges.length)),
    [conceptCount, graph.nodes.length, relationshipCount, graph.edges.length],
  );
  const empty = memoryCount === 0 && conceptCount === 0;

  return (
    <section className="brain-hero" data-testid="brain-knowledge-flow" aria-labelledby="brain-home-title">
      <div
        className="brain-hero-organism"
        data-growth={readiness.state}
        data-stage={growth.stage}
        data-testid="brain-hero-organism"
      >
        {graphViewMode === "3d" && graph.nodes.length > 0 ? (
          <div className="brain-hero-3d-backdrop" aria-hidden="true">
            <React.Suspense fallback={null}>
              <Brain3DCanvas
                nodes={graph.nodes}
                edges={graph.edges}
                showSuperseded={showSuperseded}
                firingTrace={activeFiringTrace}
                intensity={intensity}
                variant="ambient"
                language={language}
              />
            </React.Suspense>
          </div>
        ) : null}
        <i className="brain-growth-ring is-inner" aria-hidden="true" />
        <i className="brain-growth-ring is-mid" aria-hidden="true" />
        <i className="brain-growth-ring is-outer" aria-hidden="true" />
        <div className="brain-hero-orb">
          <LivingBrain
            state={brainState}
            intensity={intensity}
            size="normal"
            depth={readiness.depth}
            showLabel={false}
            onInteract={onExploreBrain}
          />
        </div>
      </div>
      <p className="brain-hero-growing">{t(language, "brain.home.growing")}</p>

      <div className="brain-hero-header-text">
        <h1 id="brain-home-title">{t(language, "brain.home.askTitle")}</h1>
        {empty ? (
          <p className="brain-hero-line">{t(language, "brain.hero.empty")}</p>
        ) : (
          <p className="brain-hero-line">
            {t(language, "brain.hero.line")}
            <BrainStatsBadge
              language={language}
              memories={memoryCount}
              concepts={conceptCount}
              relationships={relationshipCount}
              onExploreBrain={onExploreBrain}
            />
          </p>
        )}
      </div>

      {trailing ? <div className="brain-hero-trailing">{trailing}</div> : null}
    </section>
  );
});

export function BrainStatsBadge({
  language,
  memories,
  concepts,
  relationships,
  onExploreBrain,
}: {
  language: Language;
  memories: number;
  concepts: number;
  relationships: number;
  onExploreBrain: () => void;
}) {
  const [open, setOpen] = React.useState(false);
  const rootRef = React.useRef<HTMLSpanElement>(null);
  const closeTimer = React.useRef<number | null>(null);
  const popoverId = React.useId();
  const openedAt = React.useRef(0);

  const cancelClose = () => {
    if (closeTimer.current !== null) {
      window.clearTimeout(closeTimer.current);
      closeTimer.current = null;
    }
  };
  const scheduleClose = () => {
    cancelClose();
    closeTimer.current = window.setTimeout(() => setOpen(false), 160);
  };

  React.useEffect(() => {
    if (!open) return;
    const dismiss = (event: Event) => {
      const target = event.target as Node | null;
      if (target && rootRef.current?.contains(target)) return;
      setOpen(false);
    };
    document.addEventListener("pointerdown", dismiss, true);
    document.addEventListener("focusin", dismiss, true);
    return () => {
      document.removeEventListener("pointerdown", dismiss, true);
      document.removeEventListener("focusin", dismiss, true);
    };
  }, [open]);

  React.useEffect(() => cancelClose, []);

  const rows = [
    { key: "memories", label: t(language, "brain.hero.stats.memories"), value: memories, tone: "is-memory" },
    { key: "concepts", label: t(language, "brain.hero.stats.concepts"), value: concepts, tone: "is-knowledge" },
    { key: "relationships", label: t(language, "brain.hero.stats.relationships"), value: relationships, tone: "is-core" },
  ];
  const max = Math.max(1, ...rows.map((row) => row.value));

  return (
    <span
      ref={rootRef}
      className="brain-hero-stats"
      data-testid="brain-hero-stats"
      onPointerEnter={(event) => {
        if (event.pointerType !== "mouse") return;
        cancelClose();
        setOpen((current) => {
          if (!current) openedAt.current = Date.now();
          return true;
        });
      }}
      onPointerLeave={(event) => {
        if (event.pointerType !== "mouse") return;
        scheduleClose();
      }}
      onKeyDown={(event) => {
        if (event.key !== "Escape") return;
        event.preventDefault();
        setOpen(false);
      }}
    >
      <button
        type="button"
        className="brain-hero-stats-badge"
        aria-expanded={open}
        aria-controls={open ? popoverId : undefined}
        aria-label={t(language, "brain.hero.stats.aria", { memories, concepts })}
        onClick={() => {
          setOpen((current) => {
            if (!current) {
              openedAt.current = Date.now();
              return true;
            }
            return Date.now() - openedAt.current < 350;
          });
        }}
      >
        <span className="brain-hero-stat">
          {t(language, "brain.hero.stats.memories")} <strong>{memories}</strong>
        </span>
        <i aria-hidden="true" />
        <span className="brain-hero-stat">
          {t(language, "brain.hero.stats.concepts")} <strong>{concepts}</strong>
        </span>
      </button>

      {open ? (
        <span
          id={popoverId}
          className="brain-hero-stats-popover"
          role="group"
          aria-label={t(language, "brain.hero.stats.popoverAria")}
          data-testid="brain-hero-stats-popover"
        >
          <strong className="brain-hero-stats-title">{t(language, "brain.hero.stats.title")}</strong>
          <span className="brain-hero-stats-graph" aria-hidden="true">
            {rows.map((row) => (
              <span key={row.key} className="brain-hero-stats-row">
                <span className="brain-hero-stats-label">{row.label}</span>
                <span className="brain-hero-stats-track">
                  <span
                    className={`brain-hero-stats-fill ${row.tone}`}
                    style={{ width: `${Math.max(6, Math.round((row.value / max) * 100))}%` }}
                  />
                </span>
                <span className="brain-hero-stats-value">{row.value}</span>
              </span>
            ))}
          </span>
          <span className="brain-hero-stats-hint">{t(language, "brain.hero.hint")}</span>
          <button type="button" className="brain-hero-stats-cta" onClick={onExploreBrain}>
            <Map className="h-3.5 w-3.5" aria-hidden="true" />
            {t(language, "brain.hero.stats.mapCta")}
          </button>
        </span>
      ) : null}
    </span>
  );
}
