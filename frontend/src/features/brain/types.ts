import type { BrainState } from "@/components/LivingBrain";

export type ApiRecord = Record<string, unknown>;
export type BrainDepth = 1 | 2 | 3 | 4 | 5;

export type Message = {
  role: "user" | "assistant";
  content: string;
  proof?: MessageProof;
  // Real files the assistant created in the local workspace for this reply.
  files?: MessageFile[];
};

export type MessageFile = {
  path: string;
  filename: string;
  bytes: number;
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
  // Optional unix epoch (ms) the concept was added to the graph. Enables
  // time-based exploration; absent when the backend does not yet emit it.
  createdAt?: number;
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

export type BrainBriefAction = {
  id: "add_source" | "ask_brain" | "inspect_topics" | "verify_model" | "backup_brain" | string;
  labelKey: string;
  detailKey: string;
  route: string;
  priority: number;
};

export type BrainBriefEvidence = {
  id: string;
  labelKey: string;
  value: number;
  detailKey: string;
};

export type BrainBrief = {
  status: "quiet" | "forming" | "alive" | string;
  score: number;
  headlineKey: string;
  bodyKey: string;
  focus: {
    kind: string;
    title: string;
    detail: string;
    source: string;
    score: number;
    empty: boolean;
  };
  nextActions: BrainBriefAction[];
  evidence: BrainBriefEvidence[];
  generatedAt: string;
};

export type IngestionSourceType = "file" | "folder" | "note" | "web";

export type IngestionPipelineStage =
  | "preparing"
  | "parsing"
  | "embedding"
  | "indexing"
  | "complete"
  | "error";

export type IngestionState = {
  sourceType: IngestionSourceType;
  label: string;
  stage: IngestionPipelineStage;
  startedAt: number;
  completedAt: number | null;
  newMemories: number;
  newEntities: number;
  error?: string;
};

export type EmergenceEvent = {
  id: string;
  sourceType: IngestionSourceType;
  label: string;
  newMemories: number;
  newEntities: number;
  at: number;
};

export const INGESTION_STAGE_ORDER: IngestionPipelineStage[] = [
  "preparing",
  "parsing",
  "embedding",
  "indexing",
  "complete",
];

export const DEPTHS: Array<{ level: BrainDepth; labelKey: string; state: BrainState }> = [
  { level: 1, labelKey: "brain.depthLabel.1", state: "idle" },
  { level: 2, labelKey: "brain.depthLabel.2", state: "recalling" },
  { level: 3, labelKey: "brain.depthLabel.3", state: "synthesizing" },
  { level: 4, labelKey: "brain.depthLabel.4", state: "planning" },
  { level: 5, labelKey: "brain.depthLabel.5", state: "synthesizing" },
];
