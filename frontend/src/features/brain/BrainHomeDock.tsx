import * as React from "react";
import { createPortal } from "react-dom";
import { ChartSpline, History, Network, SlidersHorizontal, X } from "lucide-react";

import type { BrainState } from "@/components/LivingBrain";
import { t, type Language } from "@/i18n";
import { useFocusTrap } from "@/lib/useFocusTrap";
import { useAppStore } from "@/store/appStore";
import { DailyBriefingPanel } from "@/features/command/DailyBriefingPanel";
import { PendingProposalsPanel } from "@/features/command/PendingProposalsPanel";
import type {
  BrainBrief,
  BrainDepth,
  BrainProactiveAction,
  BrainProactiveActivity,
  BrainProof,
  BrainReadiness,
  ConversationSummary,
  EmergenceEvent,
  KnowledgeConcept,
  MemoryFragment,
} from "./types";
import { BrainCarePanel } from "./BrainCarePanel";
import { BrainFeaturesPanel } from "./BrainFeaturesPanel";
import { BrainIntelligencePanel } from "./BrainIntelligencePanel";
import { KnowledgeGardenPanel } from "./KnowledgeGarden";
import { BrainMemoryAutomation } from "./BrainKnowledgeFlow";
import { BrainOverviewPanel } from "./BrainOverviewPanel";
import { IngestionJobsPanel, WatchHealthCard } from "./BrainSignals";
import { IngestionTimelineSection } from "./IngestionPanels";
import { MemoryRings } from "./MemoryRings";
import {
  BrainBriefPanel,
  handleBriefAction,
  ModelContinuityDemo,
  PastConversationsPanel,
} from "./HomePanels";

export type DockTab = "conversations" | "stats" | "map" | "features";

const TAB_LABEL_KEY: Record<DockTab, string> = {
  conversations: "brain.dock.tab.conversations",
  stats: "brain.dock.tab.stats",
  map: "brain.dock.tab.map",
  features: "brain.dock.tab.features",
};

const TAB_ICON: Record<DockTab, typeof History> = {
  conversations: History,
  stats: ChartSpline,
  map: Network,
  features: SlidersHorizontal,
};

/**
 * The home's support surfaces — past conversations, the stats panels the Brain
 * keeps, and the memory map — moved off the main canvas onto a dock: a quiet
 * rail that opens a drawer. The canvas keeps only the composer and its guide
 * cards; everything else is one deliberate click (or tap) away instead of a
 * stack of cards competing with the first move.
 */
export function BrainHomeDock({
  language,
  brainState,
  intensity,
  readiness,
  memories,
  concepts,
  relationshipCount,
  emergenceEvents,
  proactiveActivities,
  pastConversations,
  historyBusyId,
  streaming,
  modelName,
  proof,
  brief,
  onOpenDepth,
  onExploreBrain,
  onVerifyModelContinuity,
  onProactiveAction,
  onResumeConversation,
  onDeleteConversation,
  onRequestDetails,
}: {
  language: Language;
  brainState: BrainState;
  intensity: number;
  readiness: BrainReadiness;
  memories: MemoryFragment[];
  concepts: KnowledgeConcept[];
  relationshipCount: number;
  emergenceEvents: EmergenceEvent[];
  proactiveActivities: BrainProactiveActivity[];
  pastConversations: ConversationSummary[];
  historyBusyId: string | null;
  streaming: boolean;
  modelName: string;
  proof: BrainProof;
  brief: BrainBrief;
  onOpenDepth: (depth: BrainDepth) => void;
  onExploreBrain: () => void;
  onVerifyModelContinuity: () => void;
  onProactiveAction: (action: BrainProactiveAction) => void;
  onResumeConversation: (id: string) => void;
  onDeleteConversation: (id: string) => void;
  onRequestDetails: () => void;
}) {
  const mode = useAppStore((state) => state.mode);
  const isBasic = mode === "basic";
  const [tab, setTab] = React.useState<DockTab | null>(null);
  const open = tab !== null;
  const trapRef = useFocusTrap<HTMLElement>(() => {
    // Escape closes the innermost surface first. The memory-ring peek closes
    // itself on a *window* keydown — which the trap's stopPropagation starves —
    // so close it here and keep the drawer under it open.
    const peek = document.getElementById("brain-ring-peek");
    if (peek) {
      peek.querySelector<HTMLButtonElement>("header button")?.click();
      return;
    }
    setTab(null);
  }, open);

  const openTab = (next: DockTab) => {
    // The stats and map panels read from queries the proof hook prefetches on
    // demand — same contract the old insights shelf honoured with onToggle.
    // 기능 is not one of them: the switchboard reads `/api/features` and
    // nothing else, so prefetching the whole proof bundle for it would be work
    // nobody asked for.
    if (next !== "conversations" && next !== "features") onRequestDetails();
    setTab((current) => (current === next && open ? null : next));
  };

  const tabs: DockTab[] = ["conversations", "stats", "map", "features"];

  return (
    <div className="brain-home-dock" data-testid="brain-home-dock">
      <div className="brain-home-dock-rail" role="group" aria-label={t(language, "brain.dock.aria")}>
        {tabs.map((id) => {
          const Icon = TAB_ICON[id];
          return (
            <button
              key={id}
              type="button"
              className={id === "conversations" ? "is-continuity" : undefined}
              data-testid={`brain-dock-${id}`}
              aria-expanded={open && tab === id}
              onClick={() => openTab(id)}
            >
              <Icon className="h-4 w-4" aria-hidden="true" />
              <span>{t(language, TAB_LABEL_KEY[id])}</span>
              {id === "conversations" && pastConversations.length ? (
                <small className="brain-dock-count">{pastConversations.length}</small>
              ) : null}
            </button>
          );
        })}
      </div>

      {/* Portaled to <body>: the home card sits under a backdrop-filter, which
          makes every fixed descendant stack inside it — below the sticky
          topbar. As a body child the layer's z-index is judged at root level
          and the drawer actually covers the screen it claims to. */}
      {open ? createPortal(
        <div className="brain-home-dock-layer">
          <div className="brain-home-dock-scrim" aria-hidden="true" onClick={() => setTab(null)} />
          <aside
            ref={trapRef}
            className="brain-home-drawer"
            role="dialog"
            aria-modal="true"
            aria-label={t(language, "brain.dock.drawerAria")}
            data-testid="brain-home-drawer"
          >
            <header className="brain-home-drawer-head">
              <div className="brain-home-drawer-tabs" role="group" aria-label={t(language, "brain.dock.aria")}>
                {tabs.map((id) => (
                  <button
                    key={id}
                    type="button"
                    aria-pressed={tab === id}
                    className={tab === id ? "is-active" : ""}
                    data-testid={`brain-drawer-tab-${id}`}
                    onClick={() => openTab(id)}
                  >
                    {t(language, TAB_LABEL_KEY[id])}
                  </button>
                ))}
              </div>
              <button
                type="button"
                className="brain-home-drawer-close"
                aria-label={t(language, "brain.home.shelf.close")}
                onClick={() => setTab(null)}
              >
                <X className="h-4 w-4" aria-hidden="true" />
              </button>
            </header>

            <div className="brain-home-drawer-body">
              {tab === "conversations" ? (
                <PastConversationsPanel
                  language={language}
                  items={pastConversations}
                  busyId={historyBusyId}
                  onResume={onResumeConversation}
                  onDelete={onDeleteConversation}
                />
              ) : null}

              {tab === "stats" ? (
                <>
                  <BrainMemoryAutomation
                    language={language}
                    brief={brief}
                    activities={proactiveActivities}
                    streaming={streaming}
                    onAction={onProactiveAction}
                  />
                  <BrainBriefPanel
                    language={language}
                    brief={brief}
                    showEvidence={!isBasic}
                    onAction={(action) => handleBriefAction(action, onVerifyModelContinuity)}
                  />
                  {isBasic ? null : (
                    <>
                      <IngestionTimelineSection language={language} emergenceEvents={emergenceEvents} />
                      <ModelContinuityDemo
                        language={language}
                        proof={proof}
                        modelName={modelName}
                        onVerify={onVerifyModelContinuity}
                      />
                      <BrainOverviewPanel
                        memories={memories}
                        concepts={concepts}
                        readiness={readiness}
                        proof={proof}
                        onOpenDepth={onOpenDepth}
                      />
                    </>
                  )}
                  <DailyBriefingPanel language={language} variant="home" />
                  <WatchHealthCard language={language} />
                  <IngestionJobsPanel language={language} />
                  <PendingProposalsPanel language={language} />
                  <BrainIntelligencePanel language={language} />
                  <BrainCarePanel language={language} />
                </>
              ) : null}

              {tab === "map" ? (
                <>
                  <MemoryRings
                    language={language}
                    brainState={brainState}
                    intensity={intensity}
                    readiness={readiness}
                    memories={memories}
                    concepts={concepts}
                    relationshipCount={relationshipCount}
                    onExploreBrain={onExploreBrain}
                    onOpenDepth={onOpenDepth}
                  />
                  <KnowledgeGardenPanel language={language} />
                  <button type="button" className="brain-home-drawer-map-cta" onClick={onExploreBrain}>
                    <Network className="h-4 w-4" aria-hidden="true" />
                    {t(language, "brain.hero.stats.mapCta")}
                  </button>
                </>
              ) : null}

              {tab === "features" ? <BrainFeaturesPanel language={language} /> : null}
            </div>
          </aside>
        </div>,
        document.body,
      ) : null}
    </div>
  );
}
