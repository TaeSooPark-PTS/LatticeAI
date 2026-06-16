import type { BrainDepth, KnowledgeConcept, KnowledgeGraphModel, MemoryFragment, RelationshipThread } from "./types";
import { BrainGraphLayer, BrainKnowledgeLayer } from "./BrainGraphLayer";
import { BrainMemoryLayer } from "./BrainMemoryLayer";
import { BrainRelationshipLayer } from "./BrainRelationshipLayer";

export function DepthEmergence({
  depth,
  memories,
  concepts,
  relationships,
  graphModel,
  graphSearch,
  selectedGraphId,
  onGraphSearch,
  onSelectGraphNode,
  onRecallMemory,
}: {
  depth: BrainDepth;
  memories: MemoryFragment[];
  concepts: KnowledgeConcept[];
  relationships: RelationshipThread[];
  graphModel: KnowledgeGraphModel;
  graphSearch: string;
  selectedGraphId: string | null;
  onGraphSearch: (value: string) => void;
  onSelectGraphNode: (id: string | null) => void;
  onRecallMemory: (fragment: MemoryFragment) => void;
}) {
  if (depth === 1) return null;

  return (
    <>
      {depth >= 2 ? (
        <BrainMemoryLayer memories={memories} depth={depth} onRecallMemory={onRecallMemory} />
      ) : null}
      {depth >= 3 && depth < 5 ? (
        <BrainKnowledgeLayer concepts={concepts} depth={depth} />
      ) : null}
      {depth >= 4 && depth < 5 ? (
        <BrainRelationshipLayer concepts={concepts} relationships={relationships} />
      ) : null}
      {depth >= 5 ? (
        <BrainGraphLayer
          model={graphModel}
          search={graphSearch}
          selectedId={selectedGraphId}
          onSearch={onGraphSearch}
          onSelect={onSelectGraphNode}
        />
      ) : null}
    </>
  );
}
