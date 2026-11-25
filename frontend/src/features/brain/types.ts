export type ApiRecord = Record<string, unknown>;
export type BrainDepth = 1 | 2 | 3 | 4 | 5;

export type Message = {
  role: "user" | "assistant";
  content: string;
  proof?: MessageProof;
  files?: MessageFile[];
  contextQuality?: MessageContextQuality;
  agentState?: string;
  approval?: MessageApproval;
  grounding?: MessageGrounding;
  agentSteps?: AgentStepEvent[];
  loopSummary?: MessageLoopSummary;
  runExplanation?: MessageRunExplanation;
  hybridContext?: MessageHybridContext;
  cloudAnswer?: MessageCloudAnswer;
};

export type MessageHybridContext = {
  nodeIds: string[];
  keywords: string[];
};

export type MessageKgExpansion = {
  status: string;
  candidateCount: number;
  stagedForReview: boolean;
};

export type MessageCloudAnswer = {
  provider: string;
  model: string;
  sentNodeCount: number;
  expansion: MessageKgExpansion | null;
};

export type AgentStepEvent = {
  phase: string;
  event: string;
  action?: string;
  path?: string;
  step?: number;
  ok?: boolean;
  decision?: string;
  verdict?: string;
  state?: string;
  detail?: string;
};

export type MessageLoopSummary = {
  repairs: Record<string, number>;
  parseErrors: number;
  parseRecovered: number;
  total: number;
};

export type MessageRunExplanation = {
  code: string;
  ok: boolean;
  headline: string;
  details: string[];
  strainLevel: "none" | "light" | "moderate" | "heavy";
};

export type ApprovalStatus =
  | "pending"
  | "approved"
  | "cancelled"
  | "expired"
  | "error";

export type MessageApproval = {
  runId: string;
  token: string;
  expiresAt: string;
  planSummary: string;
  plan: Record<string, unknown> | null;
  status: ApprovalStatus;
  errorReason?: string;
  replanMessage?: string;
};

export type PendingApprovalSummary = {
  runId: string;
  goal: string;
  expiresAt: string;
};

export type MessageClaimVerification = {
  claimSupportRatio: number;
  citationPrecision: number;
  citationRecall: number;
  supersededCitationLeaks: number;
  numericMismatches: number;
  polarityMismatches: number;
  shouldAbstain: boolean;
  abstentionReason: string | null;
};

export type MessageGrounding = {
  status: "supported" | "unsupported" | "no_context" | string;
  reason: string | null;
  claimVerification?: MessageClaimVerification;
};

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
  repaired?: boolean;
  previewable?: boolean;
  brainIngest?: MessageBrainIngest;
};

export type MessageBrainIngest = {
  status: "ok" | "pending" | "failed" | string;
  detail?: string;
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
    matchedTerms: string[];
    confidence: EvidenceConfidence;
    score: number;
    locator: string;
    kind?: string;
    caption?: string;
    thumbnail?: string;
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

export type ConversationSummary = {
  id: string;
  title: string;
  messageCount: number;
  updatedAt?: number;
};

export type KnowledgeConcept = {
  id: string;
  label: string;
  type: string;
  summary: string;
  importance: number;
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
      locator: string;
      kind?: string;
      caption?: string;
      thumbnail?: string;
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

export type IngestionWatchError = {
  path: string;
  detail: string;
};

export type IngestionWatch = {
  id: string;
  path: string;
  enabled: boolean;
  lastScanAt: string;
  lastResult: {
    status: string;
    ingested: number;
    failed: number;
  } | null;
  trackedFiles: number;
  lastErrors: IngestionWatchError[];
};

export type IngestionWatchStatus = {
  enabledCount: number;
  polling: boolean;
  intervalSeconds: number;
  watches: IngestionWatch[];
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

