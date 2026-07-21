import type { BrainState } from "@/components/LivingBrain";

export type ApiRecord = Record<string, unknown>;
export type BrainDepth = 1 | 2 | 3 | 4 | 5;

export type Message = {
  role: "user" | "assistant";
  content: string;
  proof?: MessageProof;
  // Real files the assistant created in the local workspace for this reply.
  files?: MessageFile[];
  // Honest signal about how much graph context backed this answer.
  contextQuality?: MessageContextQuality;
  // Terminal agent-loop state for this reply (NEEDS_REVIEW/FAILED render as
  // warnings, never as success).
  agentState?: string;
  // Interactive plan approval (status "awaiting_approval"): the run is paused
  // server-side behind a short-TTL single-use token until the user decides.
  approval?: MessageApproval;
  // Answer-citation binding verdict ("근거 있음/근거 없음") from the backend.
  grounding?: MessageGrounding;
};

export type ApprovalStatus =
  | "pending"
  | "approved"
  | "cancelled"
  | "expired"
  | "error";

// A paused agent run waiting for the user's go-ahead. `token` is single-use
// with a ~10 minute server TTL; `plan` is the normalized plan the user may
// edit before resuming.
export type MessageApproval = {
  runId: string;
  token: string;
  expiresAt: string;
  planSummary: string;
  plan: Record<string, unknown> | null;
  status: ApprovalStatus;
  errorReason?: string;
};

// Honest answer-grounding signal: whether the reply actually used retrieved
// sources. `no_context` means nothing was retrieved at all.
export type MessageGrounding = {
  status: "supported" | "unsupported" | "no_context" | string;
  reason: string | null;
};

// Additive chat meta ("context_quality") flowing on the same channel as
// sources/evidence. `limited` means the graph context was thinner than usual.
export type MessageContextQuality = {
  mode: "hybrid" | "lexical_only" | "none" | string;
  nodes: number;
  limited: boolean;
  reason: string | null;
};

export type MessageFile = {
  path: string;
  filename: string;
  bytes: number;
  // True when the generation pipeline had to fall back to a deterministic
  // repair scaffold — the UI badges these so they are never oversold.
  repaired?: boolean;
  // From the artifacts[] response contract: whether the backend judged this
  // file safe/sensible to preview inline. Undefined when the artifact meta is
  // absent (older responses) — the UI then falls back to an extension check.
  previewable?: boolean;
};

export type EvidenceConfidence = "high" | "medium" | "low";

export type MessageProof = {
  query: string;
  model: string;
  provenAcrossModels: boolean;
  citations: Array<{
    id: string;
    source: string;
    title: string;
    snippet: string;
    // Evidence explainability: why the Brain picked this citation.
    matchedTerms: string[];
    confidence: EvidenceConfidence;
    score: number;
  }>;
};

export type MemoryFragment = {
  id: string;
  title: string;
  kind: string;
  detail?: string;
  tags: string[];
  agentGenerated: boolean;
};

// A past conversation the user can resume from the Brain home.
export type ConversationSummary = {
  id: string;
  title: string;
  messageCount: number;
  // Unix epoch (ms) of the latest activity; undefined when the backend omits it.
  updatedAt?: number;
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
      matchedTerms: string[];
      confidence: EvidenceConfidence;
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

export type BrainSuggestedQuestion = {
  id: string;
  labelKey: string;
  detailKey: string;
  promptKey: string;
  params: Record<string, string | number>;
  priority: number;
};

export type BrainBriefEvidence = {
  id: string;
  labelKey: string;
  value: number;
  detailKey: string;
};

export type BrainProactiveAction = {
  id: string;
  intent: "ask" | "delegate" | "review" | "route" | string;
  labelKey: string;
  detailKey: string;
  prompt: string;
  route: string;
  priority: number;
  context: Record<string, string | number>;
};

export type BrainProactiveActivity = {
  id: string;
  actionId: string;
  labelKey: string;
  intent: string;
  status: "running" | "completed" | "failed";
  startedAt: number;
  completedAt?: number;
  detail?: string;
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
  suggestedQuestions: BrainSuggestedQuestion[];
  proactiveActions: BrainProactiveAction[];
  evidence: BrainBriefEvidence[];
  generatedAt: string;
};

export type IngestionSourceType = "chat" | "file" | "folder" | "note" | "web";

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
  nodeIds?: string[];
  chunkCount?: number;
  duplicate?: boolean;
  provenanceId?: string;
  error?: string;
  extraction?: ExtractionQuality;
};

export type IngestionEvidence = {
  nodeIds: string[];
  chunkCount: number;
  duplicate?: boolean;
  provenanceId?: string;
  extraction?: ExtractionQuality;
};

// Additive ingest meta ("extraction_quality"): how well the source content
// could be extracted. Low quality carries user-facing warnings.
export type ExtractionQuality = {
  score: number;
  level: "high" | "medium" | "low";
  reasons: string[];
  warnings: string[];
};

export type VectorFreshness = {
  status: "ready" | "pending" | "unavailable" | string;
  pendingItems: number;
  totalItems: number;
  detail: string;
};

export type IngestionJobStatus = "queued" | "running" | "completed" | "failed" | "partial" | string;

export type IngestionJob = {
  jobId: string;
  status: IngestionJobStatus;
  total: number;
  processed: number;
  failed: number;
  errors: string[];
  createdAt?: string;
  updatedAt?: string;
};

export type EmergenceEvent = {
  id: string;
  sourceType: IngestionSourceType;
  label: string;
  newMemories: number;
  newEntities: number;
  nodeIds: string[];
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
