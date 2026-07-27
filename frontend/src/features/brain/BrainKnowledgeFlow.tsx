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
  MoreHorizontal,
  Network,
  SearchCheck,
  ShieldCheck,
  Sparkles,
  X,
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

// Node cards are centred on their point (`translate(-50%, -50%)`) and are up to
// 8.2rem wide by 2.75rem tall — roughly 12% of the canvas width and 17% of its
// height. Two cards therefore only avoid each other when they clear that on at
// least one axis. The previous layout packed all eight into x 62–94%, i.e. under
// three card-widths of room, so with real Korean labels they overlapped in a
// dozen pairs. These rows are 27% apart vertically, which separates every pair
// across rows no matter their x, and 15–16% apart horizontally within a row.
// Keep x within 7–93 so a centred card cannot be clipped by the canvas, and y
// at or under 61 so the bottom row stays clear of the live-status bar, which
// covers roughly the last 27% of the shortened home canvas.
const GRAPH_POSITIONS = [
  { x: 62, y: 26 },
  { x: 78, y: 26 },
  { x: 93, y: 26 },
  { x: 62, y: 45 },
  { x: 78, y: 45 },
  { x: 93, y: 45 },
  { x: 70, y: 64 },
  { x: 86, y: 64 },
] as const;


/**
 * A title a person would recognise, or nothing.
 *
 * Generated identifiers (`brain-1782904609263`, bare uuids, `doc_8837261`) reach
 * these slots whenever a conversation or source has no subject yet. They are not
 * titles, so callers should fall through to the next candidate rather than print
 * them.
 */
function humanTitle(value: unknown): string {
  const text = String(value ?? "").trim();
  if (!text) return "";
  if (/^[A-Za-z][\w-]*[-_]?\d{6,}$/.test(text)) return "";
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(text)) return "";
  if (/^\d+$/.test(text)) return "";
  return text;
}

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
  // Titles fall back through several sources, and one of them (a conversation
  // with no subject yet) is a generated id like `brain-1782904609263`. Printing
  // it told the reader "brain-1782904609263 주변의 기억을 지키는 중", which is
  // noise wearing the clothes of a title. Skip id-shaped values and keep falling
  // through to something a person actually wrote.
  const sourceLabel = humanTitle(activeIngestion?.label)
    || humanTitle(latestEvent?.label)
    || humanTitle(brief.focus.title)
    || humanTitle(memories[0]?.title)
    || t(language, "brain.flow.source.empty");
  const statusText = activeIngestion
    ? t(language, `brain.ingest.stage.${activeIngestion.stage}.hint`)
    : latestEvent
      ? t(language, "brain.flow.status.emerged", {
          memories: latestEvent.newMemories,
          entities: latestEvent.newEntities,
        })
      : nodes.length
        ? t(language, "brain.flow.status.ready", {
            focus: humanTitle(brief.focus.title) || humanTitle(nodes[0]?.label) || "Brain",
          })
        : t(language, "brain.flow.status.empty");
  const memoryCount = Math.max(readiness.signals.memoryCount, memories.length);
  const conceptCount = Math.max(readiness.signals.conceptCount, graph.nodes.length);
  const relationshipCount = Math.max(readiness.signals.relationshipCount, graph.edges.length);

  return (
    <section
      className={`brain-knowledge-flow ${absorbing ? "is-absorbing" : ""}`}
      data-source={sourceType || "idle"}
      data-stage={activeIngestion?.stage || (latestEvent ? "connected" : "ready")}
      data-brain-state={brainState}
      aria-labelledby="brain-home-title"
    >
      <header className="brain-flow-heading">
        <div className="brain-flow-heading-copy">
          <span className="brain-home-kicker">{t(language, "brain.home.kicker")}</span>
          <h1 id="brain-home-title">{t(language, "brain.firstScreen.title")}</h1>
          <p>{t(language, "brain.firstScreen.body")}</p>
        </div>
        <div className="brain-flow-vital" data-state={brainState}>
          <span aria-hidden="true"><i /><i /></span>
          <span>
            <small>{t(language, "brain.flow.vital")}</small>
            <strong>{t(language, `brain.living.state.${brainState}`)}</strong>
          </span>
        </div>
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
          {nodes.length ? Array.from({ length: 3 }).map((_, index) => (
            <circle key={`output-${index}`} className="brain-flow-particle is-output" r="0.66">
              <animateMotion
                begin={`${index * 1.15}s`}
                dur={absorbing ? "2.1s" : "4.2s"}
                repeatCount="indefinite"
                path="M51 50 C58 46 61 44 70 42"
              />
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
          <span className="brain-flow-organism-vital" aria-hidden="true">
            <i />
            {t(language, `brain.living.state.${brainState}`)}
          </span>
        </div>

        <div className="brain-flow-graph" aria-label={t(language, "brain.flow.graph.aria", { concepts: conceptCount, relationships: relationshipCount })}>
          <span className="brain-flow-column-label">{t(language, "brain.flow.graph")}</span>
          <svg className="brain-flow-edges" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
            {edges.map((edge, index) => {
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
                  style={{ animationDelay: `${index * -0.72}s` }}
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

        <div className="brain-flow-live-status" role="status" aria-live="polite" aria-atomic="true">
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
      </div>
    </section>
  );
});

export function BrainMemoryAutomation({
  language,
  brief,
  activities,
  streaming,
  onAction,
  compact = false,
}: {
  language: Language;
  brief: BrainBrief;
  activities: BrainProactiveActivity[];
  streaming: boolean;
  onAction: (action: BrainProactiveAction) => void;
  compact?: boolean;
}) {
  const compactDetailsRef = React.useRef<HTMLDetailsElement>(null);
  const actions = brief.proactiveActions.slice(0, 3);
  const evidence = brief.evidence.filter((item) => item.value > 0).slice(0, 3);
  const focusTitle = brief.focus.title || t(language, "brain.brief.focus.empty");
  const closeCompactActions = () => {
    const details = compactDetailsRef.current;
    if (!details) return;
    details.removeAttribute("open");
    details.querySelector<HTMLElement>("summary")?.focus();
  };

  if (compact) {
    const primaryAction = actions[0];
    const actionCountClass = actions.length > 1 ? "has-more-actions" : actions.length === 1 ? "has-single-action" : "has-no-actions";
    return (
      <section
        className={`brain-memory-automation is-compact ${actionCountClass}`}
        data-testid="brain-automation-dock"
        aria-labelledby="brain-memory-automation-compact-title"
      >
        <div className="brain-automation-compact-head">
          <span className="brain-memory-automation-mark" aria-hidden="true"><Bot className="h-4 w-4" /></span>
          <span>
            <small>{t(language, "brain.automation.kicker")}</small>
            <strong id="brain-memory-automation-compact-title">{t(language, "brain.automation.title")}</strong>
          </span>
          {actions.length > 1 ? (
            <details
              ref={compactDetailsRef}
              className="brain-automation-more"
              data-testid="brain-automation-more"
              onKeyDown={(event) => {
                if (event.key !== "Escape") return;
                event.preventDefault();
                closeCompactActions();
              }}
            >
              <summary aria-label={t(language, "brain.automation.more")}>
                <MoreHorizontal className="h-4 w-4" aria-hidden="true" />
              </summary>
              <div className="brain-automation-more-popover">
                <header className="brain-automation-more-head">
                  <strong>{t(language, "brain.automation.title")}</strong>
                  <button
                    type="button"
                    aria-label={t(language, "brain.automation.close")}
                    onClick={closeCompactActions}
                  >
                    <X className="h-4 w-4" aria-hidden="true" />
                  </button>
                </header>
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
              </div>
            </details>
          ) : null}
        </div>
        <span className="brain-automation-compact-focus" title={focusTitle}>{focusTitle}</span>
        {primaryAction ? (
          <button
            type="button"
            className="brain-automation-primary-action"
            disabled={streaming}
            onClick={() => onAction(primaryAction)}
          >
            <span aria-hidden="true">{automationIcon(primaryAction.intent)}</span>
            <span>
              <strong>{t(language, primaryAction.labelKey)}</strong>
              <small>{t(language, automationIntentKey(primaryAction.intent))}</small>
            </span>
            <ArrowRight className="h-4 w-4" aria-hidden="true" />
          </button>
        ) : <p className="brain-automation-empty">{t(language, "brain.automation.empty")}</p>}
      </section>
    );
  }

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
