import * as React from "react";
import {
  ArrowRight,
  Bot,
  CheckCircle2,
  FileText,
  FileUp,
  FolderOpen,
  Globe2,
  Link2,
  MessageSquareText,
  Network,
  SearchCheck,
  ShieldCheck,
  Sparkles,
} from "lucide-react";

import { type BrainState, LivingBrain } from "@/components/LivingBrain";
import { t, type Language } from "@/i18n";
import type {
  BrainBrief,
  BrainProactiveAction,
  BrainProactiveActivity,
  BrainReadiness,
  EmergenceEvent,
  IngestionSourceType,
  IngestionState,
  KnowledgeGraphModel,
  MemoryFragment,
} from "./types";

const GRAPH_POSITIONS = [
  { x: 69, y: 17 },
  { x: 86, y: 23 },
  { x: 94, y: 46 },
  { x: 89, y: 73 },
  { x: 70, y: 82 },
  { x: 63, y: 57 },
  { x: 75, y: 47 },
  { x: 82, y: 60 },
] as const;

const SOURCE_ORDER: IngestionSourceType[] = ["chat", "file", "folder", "note", "web"];

const SOURCE_ICONS = {
  chat: MessageSquareText,
  file: FileUp,
  folder: FolderOpen,
  note: FileText,
  web: Globe2,
} satisfies Record<IngestionSourceType, React.ComponentType<{ className?: string; "aria-hidden"?: boolean | "true" }>>;

function isActive(state: IngestionState | null) {
  return Boolean(state && state.stage !== "complete" && state.stage !== "error");
}

export const BrainKnowledgeFlow = React.memo(function BrainKnowledgeFlow({
  language,
  brainState,
  intensity,
  graph,
  readiness,
  brief,
  memories,
  ingestionStates,
  emergenceEvents,
  streaming,
  onExploreBrain,
}: {
  language: Language;
  brainState: BrainState;
  intensity: number;
  graph: KnowledgeGraphModel;
  readiness: BrainReadiness;
  brief: BrainBrief;
  memories: MemoryFragment[];
  ingestionStates: Record<IngestionSourceType, IngestionState | null>;
  emergenceEvents: EmergenceEvent[];
  streaming: boolean;
  onExploreBrain: () => void;
}) {
  const latestEvent = emergenceEvents[0];
  const nodes = React.useMemo(() => {
    const degree = new Map<string, number>();
    for (const edge of graph.edges) {
      degree.set(edge.source, (degree.get(edge.source) ?? 0) + 1);
      degree.set(edge.target, (degree.get(edge.target) ?? 0) + 1);
    }
    const emerging = new Set(latestEvent?.nodeIds ?? []);
    return [...graph.nodes]
      .sort((left, right) => {
        const emergenceRank = Number(emerging.has(right.id)) - Number(emerging.has(left.id));
        if (emergenceRank) return emergenceRank;
        const degreeRank = (degree.get(right.id) ?? 0) - (degree.get(left.id) ?? 0);
        return degreeRank || right.importance - left.importance;
      })
      .slice(0, GRAPH_POSITIONS.length);
  }, [graph.edges, graph.nodes, latestEvent?.nodeIds]);
  const positionById = React.useMemo(
    () => new Map(nodes.map((node, index) => [node.id, GRAPH_POSITIONS[index]])),
    [nodes],
  );
  const edges = React.useMemo(
    () => graph.edges
      .filter((edge) => positionById.has(edge.source) && positionById.has(edge.target))
      .slice(0, 16),
    [graph.edges, positionById],
  );
  const activeIngestion = SOURCE_ORDER
    .map((sourceType) => ingestionStates[sourceType])
    .find((state): state is IngestionState => isActive(state));
  const emergingNodeIds = React.useMemo(() => new Set(latestEvent?.nodeIds ?? []), [latestEvent]);
  const absorbing = streaming || Boolean(activeIngestion);
  const sourceType = streaming ? "chat" : activeIngestion?.sourceType;
  const sourceLabel = activeIngestion?.label
    || latestEvent?.label
    || brief.focus.title
    || memories[0]?.title
    || t(language, "brain.flow.source.empty");
  const statusText = activeIngestion
    ? t(language, `brain.ingest.stage.${activeIngestion.stage}.hint`)
    : latestEvent
      ? t(language, "brain.flow.status.emerged", {
          memories: latestEvent.newMemories,
          entities: latestEvent.newEntities,
        })
      : nodes.length
        ? t(language, "brain.flow.status.ready", { focus: brief.focus.title || nodes[0]?.label || "Brain" })
        : t(language, "brain.flow.status.empty");
  const memoryCount = Math.max(readiness.signals.memoryCount, memories.length);
  const conceptCount = Math.max(readiness.signals.conceptCount, graph.nodes.length);
  const relationshipCount = Math.max(readiness.signals.relationshipCount, graph.edges.length);

  return (
    <section
      className={`brain-knowledge-flow ${absorbing ? "is-absorbing" : ""}`}
      data-source={sourceType || "idle"}
      aria-labelledby="brain-home-title"
    >
      <header className="brain-flow-heading">
        <span className="brain-home-kicker">{t(language, "brain.home.kicker")}</span>
        <h1 id="brain-home-title">{t(language, "brain.firstScreen.title")}</h1>
        <p>{t(language, "brain.firstScreen.body")}</p>
      </header>

      <div className="brain-flow-canvas" data-testid="brain-knowledge-flow">
        <div className="brain-flow-sources" aria-label={t(language, "brain.flow.sources.aria")}>
          <span className="brain-flow-column-label">{t(language, "brain.flow.sources")}</span>
          {SOURCE_ORDER.map((item) => {
            const Icon = SOURCE_ICONS[item];
            const state = ingestionStates[item];
            const active = item === sourceType;
            const remembered = state?.stage === "complete";
            return (
              <span
                key={item}
                className={`brain-flow-source ${active ? "is-active" : ""} ${remembered ? "is-remembered" : ""}`}
                aria-current={active ? "step" : undefined}
              >
                <Icon className="h-4 w-4" aria-hidden="true" />
                {t(language, `brain.ingest.type.${item}`)}
              </span>
            );
          })}
        </div>

        <svg className="brain-flow-transfer" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
          <path className="brain-flow-transfer-path" d="M20 50 C28 50 31 50 38 50" />
          <path className="brain-flow-transfer-path is-output" d="M54 50 C59 50 62 50 67 50" />
          {absorbing ? Array.from({ length: 4 }).map((_, index) => (
            <circle key={index} className="brain-flow-particle" r="0.8" style={{ animationDelay: `${index * 240}ms` }}>
              <animateMotion dur="1.7s" repeatCount="indefinite" path="M20 50 C28 50 31 50 43 50" />
            </circle>
          )) : null}
        </svg>

        <div className="brain-flow-organism">
          <span className="brain-flow-column-label">{t(language, "brain.flow.memory")}</span>
          <LivingBrain
            state={brainState}
            intensity={absorbing ? Math.max(intensity, 0.9) : intensity}
            size="large"
            depth={readiness.depth}
            showLabel={false}
            onInteract={onExploreBrain}
          />
        </div>

        <div className="brain-flow-graph" aria-label={t(language, "brain.flow.graph.aria", { concepts: conceptCount, relationships: relationshipCount })}>
          <span className="brain-flow-column-label">{t(language, "brain.flow.graph")}</span>
          <svg className="brain-flow-edges" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
            {edges.map((edge) => {
              const source = positionById.get(edge.source);
              const target = positionById.get(edge.target);
              if (!source || !target) return null;
              return (
                <line
                  key={edge.id}
                  x1={source.x}
                  y1={source.y}
                  x2={target.x}
                  y2={target.y}
                  vectorEffect="non-scaling-stroke"
                />
              );
            })}
          </svg>
          {nodes.length ? nodes.map((node, index) => {
            const point = GRAPH_POSITIONS[index];
            const emerging = emergingNodeIds.has(node.id);
            return (
              <button
                key={node.id}
                type="button"
                className={`brain-flow-node ${emerging ? "is-emerging" : ""}`}
                style={{ left: `${point.x}%`, top: `${point.y}%` }}
                aria-label={t(language, "brain.flow.node.aria", { label: node.label, type: node.type })}
                title={node.summary || node.type}
                onClick={onExploreBrain}
              >
                <span>{node.type}</span>
                <strong>{node.label}</strong>
              </button>
            );
          }) : (
            <div className="brain-flow-empty-graph">
              <Network className="h-5 w-5" aria-hidden="true" />
              <span>{t(language, "brain.flow.graph.empty")}</span>
            </div>
          )}
          <ul className="sr-only">
            {edges.map((edge) => {
              const source = nodes.find((node) => node.id === edge.source);
              const target = nodes.find((node) => node.id === edge.target);
              if (!source || !target) return null;
              return <li key={`accessible-${edge.id}`}>{source.label} — {edge.label} — {target.label}</li>;
            })}
          </ul>
        </div>
      </div>

      <div className="brain-flow-live-status" role="status" aria-live="polite">
        <span className="brain-flow-status-icon" aria-hidden="true">
          {absorbing ? <Sparkles className="h-4 w-4" /> : <CheckCircle2 className="h-4 w-4" />}
        </span>
        <span>
          <strong>{sourceLabel}</strong>
          <small>{statusText}</small>
        </span>
      </div>

      <ol className="brain-flow-path" aria-label={t(language, "brain.flow.path.aria")}>
        <li data-active={Boolean(sourceType)}>
          <span>{t(language, "brain.flow.path.input")}</span>
          <strong>{sourceType ? t(language, `brain.ingest.type.${sourceType}`) : t(language, "brain.flow.path.waiting")}</strong>
        </li>
        <ArrowRight className="h-4 w-4" aria-hidden="true" />
        <li data-active={absorbing}>
          <span>{t(language, "brain.flow.path.memory")}</span>
          <strong>{t(language, "brain.flow.path.memoryCount", { count: memoryCount })}</strong>
        </li>
        <ArrowRight className="h-4 w-4" aria-hidden="true" />
        <li>
          <span>{t(language, "brain.flow.path.connections")}</span>
          <strong>{t(language, "brain.flow.path.graphCount", { concepts: conceptCount, relationships: relationshipCount })}</strong>
        </li>
        <ArrowRight className="h-4 w-4" aria-hidden="true" />
        <li>
          <span>{t(language, "brain.flow.path.automation")}</span>
          <strong>{t(language, "brain.flow.path.actionCount", { count: brief.proactiveActions.length })}</strong>
        </li>
      </ol>
    </section>
  );
});

export function BrainMemoryAutomation({
  language,
  brief,
  activities,
  streaming,
  onAction,
}: {
  language: Language;
  brief: BrainBrief;
  activities: BrainProactiveActivity[];
  streaming: boolean;
  onAction: (action: BrainProactiveAction) => void;
}) {
  const actions = brief.proactiveActions.slice(0, 3);
  const evidence = brief.evidence.filter((item) => item.value > 0).slice(0, 3);
  const focusTitle = brief.focus.title || t(language, "brain.brief.focus.empty");

  return (
    <section className="brain-memory-automation" aria-labelledby="brain-memory-automation-title">
      <div className="brain-memory-automation-head">
        <div className="brain-memory-automation-mark" aria-hidden="true"><Bot className="h-5 w-5" /></div>
        <div>
          <span>{t(language, "brain.automation.kicker")}</span>
          <h2 id="brain-memory-automation-title">{t(language, "brain.automation.title")}</h2>
          <p>{t(language, "brain.automation.body")}</p>
        </div>
      </div>

      <div className="brain-automation-grounding">
        <Link2 className="h-4 w-4" aria-hidden="true" />
        <span>
          <small>{t(language, "brain.automation.basedOn")}</small>
          <strong>{focusTitle}</strong>
          {brief.focus.empty ? null : <em>{brief.focus.source}</em>}
        </span>
        {evidence.length ? (
          <div className="brain-automation-evidence" aria-label={t(language, "brain.brief.evidence.aria")}>
            {evidence.map((item) => (
              <span key={item.id} title={t(language, item.detailKey)}>
                <strong>{item.value}</strong> {t(language, item.labelKey)}
              </span>
            ))}
          </div>
        ) : null}
      </div>

      {actions.length ? (
        <div className="brain-automation-actions">
          {actions.map((action) => (
            <button key={action.id} type="button" disabled={streaming} onClick={() => onAction(action)}>
              <span className="brain-automation-action-icon" aria-hidden="true">{automationIcon(action.intent)}</span>
              <span>
                <strong>{t(language, action.labelKey)}</strong>
                <small>{t(language, action.detailKey)}</small>
              </span>
              <em>{t(language, automationIntentKey(action.intent))}</em>
              <ArrowRight className="h-4 w-4" aria-hidden="true" />
            </button>
          ))}
        </div>
      ) : (
        <p className="brain-automation-empty">{t(language, "brain.automation.empty")}</p>
      )}

      <div className="brain-automation-guard">
        <ShieldCheck className="h-4 w-4" aria-hidden="true" />
        <span>{t(language, "brain.automation.guard")}</span>
      </div>

      {activities.length ? (
        <ol className="brain-automation-activity" aria-label={t(language, "brain.proactive.trail.aria")} aria-live="polite">
          {activities.slice(0, 3).map((activity) => (
            <li key={activity.id} data-status={activity.status}>
              <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
              <strong>{t(language, activity.labelKey)}</strong>
              <span>{t(language, `brain.proactive.status.${activity.status}`)}</span>
            </li>
          ))}
        </ol>
      ) : null}
    </section>
  );
}

export function ConversationKnowledgeTrace({
  language,
  state,
  concepts,
  relationshipCount,
  onExploreBrain,
}: {
  language: Language;
  state: IngestionState | null;
  concepts: KnowledgeGraphModel["nodes"];
  relationshipCount: number;
  onExploreBrain: () => void;
}) {
  const active = isActive(state);
  return (
    <div className={`brain-conversation-trace ${active ? "is-active" : ""}`} role="status" aria-live="polite">
      <span className="brain-conversation-trace-source">
        <MessageSquareText className="h-4 w-4" aria-hidden="true" />
        {t(language, "brain.flow.conversation")}
      </span>
      <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
      <span className="brain-conversation-trace-memory">
        <Sparkles className="h-4 w-4" aria-hidden="true" />
        {state ? t(language, `brain.ingest.stage.${state.stage}.hint`) : t(language, "brain.flow.conversation.ready")}
      </span>
      <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
      <button type="button" onClick={onExploreBrain}>
        <Network className="h-4 w-4" aria-hidden="true" />
        <span>{concepts.slice(0, 3).map((concept) => concept.label).join(" · ") || t(language, "brain.flow.graph.empty.short")}</span>
        <small>{t(language, "brain.flow.relationships", { count: relationshipCount })}</small>
      </button>
    </div>
  );
}

function automationIcon(intent: BrainProactiveAction["intent"]) {
  if (intent === "delegate") return <Bot className="h-4 w-4" />;
  if (intent === "review") return <SearchCheck className="h-4 w-4" />;
  if (intent === "route") return <Network className="h-4 w-4" />;
  return <MessageSquareText className="h-4 w-4" />;
}

function automationIntentKey(intent: BrainProactiveAction["intent"]) {
  if (intent === "delegate") return "brain.automation.intent.delegate";
  if (intent === "review") return "brain.automation.intent.review";
  if (intent === "route") return "brain.automation.intent.route";
  return "brain.automation.intent.ask";
}
