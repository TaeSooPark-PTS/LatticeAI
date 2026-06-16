import type { KnowledgeConcept, RelationshipThread } from "./types";
import { layoutGraphNodes } from "./graphLayout";

export function BrainRelationshipLayer({
  concepts,
  relationships,
}: {
  concepts: KnowledgeConcept[];
  relationships: RelationshipThread[];
}) {
  const visibleConcepts = concepts.slice(0, 10);
  const layout = layoutGraphNodes(visibleConcepts, 30, 20);
  const positionById = new Map(layout.map((item) => [item.node.id, item]));
  const visibleRelationships = relationships
    .map((relationship, index) => {
      const source = positionById.get(relationship.source) || layout[index % Math.max(layout.length, 1)];
      const target = positionById.get(relationship.target) || layout[(index + 3) % Math.max(layout.length, 1)];
      return source && target && source.node.id !== target.node.id ? { relationship, source, target } : null;
    })
    .filter(Boolean)
    .slice(0, 8) as Array<{
      relationship: RelationshipThread;
      source: ReturnType<typeof layoutGraphNodes>[number];
      target: ReturnType<typeof layoutGraphNodes>[number];
    }>;

  if (!visibleRelationships.length) return null;

  return (
    <svg className="relationship-weave" viewBox="0 0 100 100" aria-hidden>
      {visibleRelationships.map(({ relationship, source, target }, index) => (
        <line
          key={`${relationship.id}-${index}`}
          x1={source.x}
          y1={source.y}
          x2={target.x}
          y2={target.y}
          style={{ animationDelay: `${index * 80}ms` }}
        />
      ))}
    </svg>
  );
}
