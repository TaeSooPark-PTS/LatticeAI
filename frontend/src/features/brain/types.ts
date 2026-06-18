import type { BrainState } from "@/components/LivingBrain";

export type ApiRecord = Record<string, unknown>;
export type BrainDepth = 1 | 2 | 3 | 4 | 5;

export type Message = {
  role: "user" | "assistant";
  content: string;
  proof?: MessageProof;
};

export type MessageProof = {
  query: string;
  model: string;
  provenAcrossModels: boolean;
  citations: Array<{
    id: string;
    source: string;
    title: string;
    snippet: string;
  }>;
};

export type MemoryFragment = {
  id: string;
  title: string;
  kind: string;
};

export type KnowledgeConcept = {
  id: string;
  label: string;
  type: string;
  summary: string;
  importance: number;
};

export type RelationshipThread = {
  id: string;
  source: string;
  target: string;
  label: string;
  weight: number;
};

export type KnowledgeGraphModel = {
  nodes: KnowledgeConcept[];
  edges: RelationshipThread[];
};

export type BrainReadiness = {
  score: number;
  state: "quiet" | "forming" | "alive";
  depth: BrainDepth;
  titleKey: string;
  actionKey: string;
  source: "memory_service" | "frontend_fallback";
  signals: {
    memoryCount: number;
    conceptCount: number;
    relationshipCount: number;
    healthySources: number;
  };
};

export type BrainProof = {
  status: "quiet" | "forming" | "alive" | string;
  modelContinuity: {
    activeModel: string;
    brainOwner: string;
    capability: boolean;
    survivesModelSwitch: boolean;
    proven: boolean;
    contextStore: string;
  };
  proofs: {
    durableItems: number;
    hasDurableEvidence: boolean;
    workspaceMemories: number;
    conversations: number;
    graphConcepts: number;
    vectorItems: number;
    healthySources: number;
  };
  recall: {
    query: string;
    count: number;
    items: Array<{
      id: string;
      source: string;
      title: string;
      snippet: string;
      score: number;
    }>;
  };
  claims: {
    canRecallUserContext: boolean;
    keepsContextAcrossModels: boolean;
    isKnowledgeStore: boolean;
  };
};

export const DEPTHS: Array<{ level: BrainDepth; labelKey: string; state: BrainState }> = [
  { level: 1, labelKey: "brain.depthLabel.1", state: "idle" },
  { level: 2, labelKey: "brain.depthLabel.2", state: "recalling" },
  { level: 3, labelKey: "brain.depthLabel.3", state: "synthesizing" },
  { level: 4, labelKey: "brain.depthLabel.4", state: "planning" },
  { level: 5, labelKey: "brain.depthLabel.5", state: "synthesizing" },
];
