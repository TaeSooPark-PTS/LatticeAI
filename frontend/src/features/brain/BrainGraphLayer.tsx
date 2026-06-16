import * as React from "react";
import { Search } from "lucide-react";
import { t } from "@/i18n";
import { useAppStore } from "@/store/appStore";
import type { KnowledgeConcept, KnowledgeGraphModel } from "./types";
import { clamp, layerStyle, layoutGraphNodes, polarPoint } from "./graphLayout";

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
  const visibleNodes = React.useMemo(() => {
    const filtered = model.nodes.filter((node) => {
      if (!query) return true;
      return `${node.label} ${node.type} ${node.summary}`.toLowerCase().includes(query);
    });
    return filtered.slice(0, 18);
  }, [model.nodes, query]);
  const layout = React.useMemo(() => layoutGraphNodes(visibleNodes, 38, 24), [visibleNodes]);
  const positionById = React.useMemo(() => new Map(layout.map((item) => [item.node.id, item])), [layout]);
  const visibleEdges = React.useMemo(
    () => model.edges.filter((edge) => positionById.has(edge.source) && positionById.has(edge.target)).slice(0, 36),
    [model.edges, positionById],
  );
  const selected = visibleNodes.find((node) => node.id === selectedId) || visibleNodes[0] || null;

  return (
    <section className="mind-core-graph" data-testid="emergent-knowledge-graph" aria-label={t(language, "brain.aria.graph")}>
      <div className="brain-graph-head">
        <div>
          <span>{t(language, "brain.level")} 5</span>
          <strong>{t(language, "brain.depth.5")}</strong>
        </div>
        <label className="brain-graph-search">
          <Search className="h-3.5 w-3.5" />
          <input
            value={search}
            onChange={(event) => onSearch(event.target.value)}
            placeholder={t(language, "brain.graph.search")}
            aria-label={t(language, "brain.graph.searchAria")}
          />
        </label>
      </div>

      {visibleNodes.length ? (
        <div className="brain-graph-canvas">
          <svg className="brain-graph-edges" viewBox="0 0 100 100" aria-hidden>
            {visibleEdges.map((edge, index) => {
              const source = positionById.get(edge.source);
              const target = positionById.get(edge.target);
              if (!source || !target) return null;
              return (
                <line
                  key={`${edge.id}-${index}`}
                  x1={source.x}
                  y1={source.y}
                  x2={target.x}
                  y2={target.y}
                  style={{ "--weight": String(clamp(edge.weight, 0.4, 2.8)) } as React.CSSProperties}
                />
              );
            })}
          </svg>
          {layout.map(({ node, x, y }, index) => (
            <button
              key={node.id}
              type="button"
              className={`graph-node ${selected?.id === node.id ? "is-selected" : ""}`}
              style={layerStyle({ "--x": `${x}%`, "--y": `${y}%`, "--delay": `${index * 35}ms` })}
              onClick={() => onSelect(node.id)}
            >
              <span>{node.type}</span>
              {node.label}
            </button>
          ))}
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
            <p>{t(language, "brain.graph.focused")}</p>
          </>
        ) : (
          <p>{t(language, "brain.graph.emptyFocus")}</p>
        )}
      </div>
    </section>
  );
}
