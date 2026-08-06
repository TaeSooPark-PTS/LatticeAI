import * as React from "react";
import { Filter, RotateCcw, Search, X } from "lucide-react";
import { t } from "@/i18n";
import { useAppStore } from "@/store/appStore";
import type { KnowledgeConcept, KnowledgeGraphModel } from "./types";
import { clamp, computeGraphNeighbors, layerStyle, layoutGraphNodes, polarPoint } from "./graphLayout";

export function BrainKnowledgeLayer({ concepts, depth }: { concepts: KnowledgeConcept[]; depth: number }) {
  const language = useAppStore((state) => state.language);
  const visible = concepts.slice(0, depth >= 4 ? 10 : 7);
  if (!visible.length) return <div className="concept-signal is-empty">{t(language, "brain.knowledge.empty")}</div>;

  return (
    <>
      {visible.map((concept, index) => {
        const point = polarPoint(index, visible.length, 24, 15, -70);
        return (
          <button
            key={concept.id}
            type="button"
            className="concept-signal"
            style={layerStyle({ "--x": `${point.x}%`, "--y": `${point.y}%`, "--delay": `${index * 45}ms` })}
            title={concept.summary || concept.type}
          >
            <span>{concept.type}</span>
            {concept.label}
          </button>
        );
      })}
    </>
  );
}

const DAY_MS = 24 * 60 * 60 * 1000;
// Time-exploration presets (days back). null == all time.
const TIME_WINDOWS: Array<{ days: number | null }> = [{ days: 7 }, { days: 30 }, { days: 90 }, { days: null }];
// A node added within this window of "now" is treated as recent for highlighting.
const RECENT_WINDOW_MS = 7 * DAY_MS;

// Highlight occurrences of `query` inside `label` by wrapping matches in <mark>.
// Exported for direct unit testing of the split/merge edge cases.
export function highlightMatch(label: string, query: string): React.ReactNode {
  if (!query) return label;
  const lower = label.toLowerCase();
  const target = query.toLowerCase();
  const parts: React.ReactNode[] = [];
  let cursor = 0;
  let matchIndex = lower.indexOf(target, cursor);
  let key = 0;
  // `target` is non-empty here (empty queries returned above), so indexOf can
  // never loop in place and the cursor always advances.
  while (matchIndex !== -1) {
    if (matchIndex > cursor) parts.push(label.slice(cursor, matchIndex));
    parts.push(<mark key={`m-${key++}`}>{label.slice(matchIndex, matchIndex + target.length)}</mark>);
    cursor = matchIndex + target.length;
    matchIndex = lower.indexOf(target, cursor);
  }
  if (cursor < label.length) parts.push(label.slice(cursor));
  return parts.length ? parts : label;
}

export function BrainGraphLayer({
  model,
  search,
  selectedId,
  onSearch,
  onSelect,
}: {
  model: KnowledgeGraphModel;
  search: string;
  selectedId: string | null;
  onSearch: (value: string) => void;
  onSelect: (id: string | null) => void;
}) {
  const language = useAppStore((state) => state.language);
  const query = search.trim().toLowerCase();

  // Control state local to the graph view so existing lifted search/selection
  // behavior (and its reset in surface()) is preserved untouched.
  const [activeTypes, setActiveTypes] = React.useState<Set<string>>(() => new Set());
  const [timeDays, setTimeDays] = React.useState<number | null>(null);
  const [typeaheadOpen, setTypeaheadOpen] = React.useState(false);

  // Distinct entity types available in the graph (for the type filter chips).
  const availableTypes = React.useMemo(() => {
    const seen = new Set<string>();
    for (const node of model.nodes) seen.add(node.type);
    return Array.from(seen).sort((a, b) => a.localeCompare(b));
  }, [model.nodes]);

  // Drop type selections that no longer exist after a graph refresh.
  React.useEffect(() => {
    setActiveTypes((prev) => {
      if (prev.size === 0) return prev;
      const next = new Set<string>();
      for (const type of prev) if (availableTypes.includes(type)) next.add(type);
      return next.size === prev.size ? prev : next;
    });
  }, [availableTypes]);

  const hasTimestamps = React.useMemo(() => model.nodes.some((node) => typeof node.createdAt === "number"), [model.nodes]);
  const timeCutoff = timeDays === null ? null : Date.now() - timeDays * DAY_MS;

  const filtersActive = activeTypes.size > 0 || timeDays !== null;

  const visibleNodes = React.useMemo(() => {
    const filtered = model.nodes.filter((node) => {
      if (activeTypes.size > 0 && !activeTypes.has(node.type)) return false;
      if (timeCutoff !== null && typeof node.createdAt === "number" && node.createdAt < timeCutoff) return false;
      if (!query) return true;
      return `${node.label} ${node.type} ${node.summary}`.toLowerCase().includes(query);
    });
    return filtered.slice(0, 18);
  }, [model.nodes, query, activeTypes, timeCutoff]);

  // Typeahead suggestions: label-prefix/substring matches ranked by importance.
  const suggestions = React.useMemo(() => {
    if (!query) return [];
    return model.nodes
      .filter((node) => node.label.toLowerCase().includes(query) || node.type.toLowerCase().includes(query))
      .slice(0, 6);
  }, [model.nodes, query]);

  const layout = React.useMemo(() => layoutGraphNodes(visibleNodes, 38, 24), [visibleNodes]);
  const positionById = React.useMemo(() => new Map(layout.map((item) => [item.node.id, item])), [layout]);
  const visibleEdges = React.useMemo(
    () => model.edges.filter((edge) => positionById.has(edge.source) && positionById.has(edge.target)).slice(0, 36),
    [model.edges, positionById],
  );
  const selected = visibleNodes.find((node) => node.id === selectedId) || visibleNodes[0] || null;
  const selectedVisibleId = selected ? selected.id : null;

  // 1-hop neighbor set for the focused node, drives focus/dim + edge highlight.
  const neighborIds = React.useMemo(
    () => (selectedId ? computeGraphNeighbors(selectedId, model.edges) : new Set<string>()),
    [selectedId, model.edges],
  );
  const focusActive = Boolean(selectedId) && neighborIds.size > 0;

  const matchedIds = React.useMemo(() => {
    if (!query) return new Set<string>();
    return new Set(visibleNodes.filter((node) => node.label.toLowerCase().includes(query)).map((node) => node.id));
  }, [visibleNodes, query]);

  function focusState(nodeId: string): "true" | "false" | undefined {
    if (!focusActive) return undefined;
    if (nodeId === selectedId || neighborIds.has(nodeId)) return "true";
    return "false";
  }

  return (
    <section className="mind-core-graph" data-testid="emergent-knowledge-graph" aria-label={t(language, "brain.aria.graph")}>
      <div className="brain-graph-head">
        <div>
          <span>{t(language, "brain.level")} 5</span>
          <strong>{t(language, "brain.depth.5")}</strong>
        </div>
        <div className="brain-graph-search-wrap">
          <label className="brain-graph-search">
            <Search className="h-3.5 w-3.5" />
            <input
              value={search}
              onChange={(event) => {
                onSearch(event.target.value);
                setTypeaheadOpen(true);
              }}
              onFocus={() => setTypeaheadOpen(true)}
              onBlur={() => window.setTimeout(() => setTypeaheadOpen(false), 120)}
              placeholder={t(language, "brain.graph.search")}
              aria-label={t(language, "brain.graph.searchAria")}
              role="combobox"
              aria-expanded={typeaheadOpen && Boolean(query)}
              aria-autocomplete="list"
              aria-controls="brain-graph-typeahead"
            />
            {search ? (
              <button
                type="button"
                className="brain-graph-search-clear"
                onClick={() => onSearch("")}
                aria-label={t(language, "brain.graph.focus.clear")}
              >
                <X className="h-3 w-3" />
              </button>
            ) : null}
          </label>
          {typeaheadOpen && query ? (
            <ul
              id="brain-graph-typeahead"
              className="brain-graph-typeahead"
              role="listbox"
              aria-label={t(language, "brain.graph.search.typeaheadAria")}
            >
              {suggestions.length ? (
                suggestions.map((node) => (
                  <li key={node.id} role="option" aria-selected={node.id === selectedId}>
                    <button
                      type="button"
                      onMouseDown={(event) => {
                        event.preventDefault();
                        onSearch(node.label);
                        onSelect(node.id);
                        setTypeaheadOpen(false);
                      }}
                    >
                      <span>{node.type}</span>
                      <strong>{highlightMatch(node.label, query)}</strong>
                    </button>
                  </li>
                ))
              ) : (
                <li className="is-empty" aria-disabled>
                  {t(language, "brain.graph.search.noMatches")}
                </li>
              )}
            </ul>
          ) : null}
        </div>
      </div>

      <div className="brain-graph-control-panel" role="group" aria-label={t(language, "brain.graph.control.panel")}>
        <div className="brain-graph-filter-row">
          <span className="brain-graph-control-label">
            <Filter className="h-3 w-3" aria-hidden />
            {t(language, "brain.graph.control.types")}
          </span>
          <div className="brain-graph-type-chips" role="group" aria-label={t(language, "brain.graph.control.types")}>
            <button
              type="button"
              className={`brain-graph-chip ${activeTypes.size === 0 ? "is-active" : ""}`}
              aria-pressed={activeTypes.size === 0}
              onClick={() => setActiveTypes(new Set())}
            >
              {t(language, "brain.graph.control.allTypes")}
            </button>
            {availableTypes.map((type) => {
              const active = activeTypes.has(type);
              return (
                <button
                  key={type}
                  type="button"
                  className={`brain-graph-chip ${active ? "is-active" : ""}`}
                  aria-pressed={active}
                  aria-label={t(language, "brain.graph.control.toggleType", { type })}
                  onClick={() =>
                    setActiveTypes((prev) => {
                      const next = new Set(prev);
                      if (next.has(type)) next.delete(type);
                      else next.add(type);
                      return next;
                    })
                  }
                >
                  {type}
                </button>
              );
            })}
          </div>
        </div>

        <div className="brain-graph-filter-row">
          <span className="brain-graph-control-label">{t(language, "brain.graph.control.time")}</span>
          <div className="brain-graph-time-chips" role="group" aria-label={t(language, "brain.graph.control.dateRange")}>
            {TIME_WINDOWS.map((window) => {
              const active = timeDays === window.days;
              const label =
                window.days === null
                  ? t(language, "brain.graph.control.allTime")
                  : t(language, "brain.graph.control.recentWindow", { days: window.days });
              return (
                <button
                  key={window.days ?? "all"}
                  type="button"
                  className={`brain-graph-chip ${active ? "is-active" : ""}`}
                  aria-pressed={active}
                  disabled={window.days !== null && !hasTimestamps}
                  onClick={() => setTimeDays(window.days)}
                >
                  {label}
                </button>
              );
            })}
          </div>
          {filtersActive ? (
            <button
              type="button"
              className="brain-graph-reset"
              onClick={() => {
                setActiveTypes(new Set());
                setTimeDays(null);
              }}
            >
              <RotateCcw className="h-3 w-3" aria-hidden />
              {t(language, "brain.graph.control.resetFilters")}
            </button>
          ) : null}
        </div>
      </div>

      {visibleNodes.length ? (
        <div className="brain-graph-canvas" data-focus-active={focusActive ? "true" : "false"}>
          <svg className="brain-graph-edges" viewBox="0 0 100 100" aria-hidden>
            {visibleEdges.map((edge, index) => {
              // visibleEdges is filtered on positionById membership above, in
              // the same render pass, so both lookups always resolve.
              const source = positionById.get(edge.source)!;
              const target = positionById.get(edge.target)!;
              const touchesFocus =
                focusActive && (edge.source === selectedId || edge.target === selectedId);
              const touchesMatch = matchedIds.has(edge.source) || matchedIds.has(edge.target);
              const highlight = touchesFocus || (query ? touchesMatch : false);
              return (
                <line
                  key={`${edge.id}-${index}`}
                  x1={source.x}
                  y1={source.y}
                  x2={target.x}
                  y2={target.y}
                  data-highlight={
                    focusActive || query ? (highlight ? "true" : "false") : undefined
                  }
                  style={{ "--weight": String(clamp(edge.weight, 0.4, 2.8)) } as React.CSSProperties}
                />
              );
            })}
          </svg>
          {layout.map(({ node, x, y }, index) => {
            const isRecent =
              typeof node.createdAt === "number" && Date.now() - node.createdAt <= RECENT_WINDOW_MS;
            return (
              <button
                key={node.id}
                type="button"
                className={`graph-node ${selectedVisibleId === node.id ? "is-selected" : ""} ${matchedIds.has(node.id) ? "is-match" : ""}`}
                data-focus={focusState(node.id)}
                data-recent={isRecent ? "true" : undefined}
                style={layerStyle({ "--x": `${x}%`, "--y": `${y}%`, "--delay": `${index * 35}ms` })}
                onClick={() => onSelect(selectedId === node.id ? null : node.id)}
              >
                <span>{node.type}</span>
                {highlightMatch(node.label, query)}
              </button>
            );
          })}
        </div>
      ) : (
        <div className="brain-graph-empty">{t(language, "brain.graph.empty")}</div>
      )}

      <div className="brain-graph-focus">
        {selected ? (
          <>
            <span>{selected.type}</span>
            <strong>{selected.label}</strong>
            <p>{selected.summary || t(language, "brain.graph.summaryFallback")}</p>
            {focusActive ? (
              <p className="brain-graph-focus-meta" role="status">
                {t(language, "brain.graph.focus.neighbors", { count: neighborIds.size })}
              </p>
            ) : (
              <p>{t(language, "brain.graph.focused")}</p>
            )}
          </>
        ) : (
          <p>{t(language, "brain.graph.emptyFocus")}</p>
        )}
      </div>
    </section>
  );
}
