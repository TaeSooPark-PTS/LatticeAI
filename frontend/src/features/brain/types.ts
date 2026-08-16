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
  // Live agent-loop step events (streamed `event: agent_step` frames) or the
  // post-hoc transcript derived from the final payload's `steps`.
  agentSteps?: AgentStepEvent[];
  // Loop transparency: how many times the model output had to be repaired.
  loopSummary?: MessageLoopSummary;
  // Plain-language outcome for an agent run (v9.9.6): why it ended the way it
  // did and how hard the model had to work to get there.
  runExplanation?: MessageRunExplanation;
  // Hybrid cloud lane: the memories sent with this turn. Kept on the message
  // so the chip can count them; the ids themselves are not rendered.
  hybridContext?: MessageHybridContext;
  // Set only when this reply came from the cloud lane (`hybrid_done`).
  cloudAnswer?: MessageCloudAnswer;
};

// Which local memories left the machine for one hybrid turn.
export type MessageHybridContext = {
  nodeIds: string[];
  keywords: string[];
};

// Knowledge-expansion proposal summary from `hybrid_done.kg_expansion`.
export type MessageKgExpansion = {
  status: string;
  candidateCount: number;
  stagedForReview: boolean;
};

// A cloud-lane answer: provider/model plus the sent-memory / expansion summary.
export type MessageCloudAnswer = {
  provider: string;
  model: string;
  sentNodeCount: number;
  expansion: MessageKgExpansion | null;
};

// One agent-loop step event. Streamed frames carry {phase, event, ...detail};
// unknown extra fields are dropped at parse time so future backend additions
// never break rendering.
export type AgentStepEvent = {
  phase: string; // plan | execute | verify | rollback | terminal | ...
  event: string; // planned | tool | proposed | blocked | parse_error | final | verdict | state | ...
  action?: string;
  path?: string;
  step?: number;
  ok?: boolean;
  decision?: string;
  verdict?: string;
  state?: string;
  detail?: string;
};

// Aggregated loop honesty meta from payload.loop: deterministic repairs the
// loop applied to model output, keyed by repair kind.
export type MessageLoopSummary = {
  repairs: Record<string, number>;
  parseErrors: number;
  parseRecovered: number;
  // Sum of repair counts + recovered parse errors — the "N회 보정" number.
  total: number;
};

// Backend `explanation` payload: an honest, deterministic sentence about how
// the run ended. `ok` is true only for a verified DONE — every other code
// renders as a caution, never as success.
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
  // The original user request, offered by the server on a 410 expiry so the
  // UI can propose "다시 계획" as a fresh chat send. Absent on client-side
  // expiry (no server round-trip happened).
  replanMessage?: string;
};

// A paused approval reported by GET /agent/approvals (survives reloads and
// server restarts). The single-use token stays with the original card — this
// summary only informs.
export type PendingApprovalSummary = {
  runId: string;
  goal: string;
  expiresAt: string;
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
  // From the payload-level `brain_ingest` field: whether this generated file
  // was actually indexed into the Brain. Absent → unknown → no chip.
  brainIngest?: MessageBrainIngest;
};

// "Brain remembered" verdict for a generated file. Only ok/pending/failed
// render a chip; any other status stays silent (never oversold).
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
    // Evidence explainability: why the Brain picked this citation.
    matchedTerms: string[];
    confidence: EvidenceConfidence;
    score: number;
    // Where inside the document this chunk came from ("Guide > Setup · p.4").
    // Empty when the chunk carries no such provenance — never guessed.
    locator: string;
    // v11.1.0 multi-modal evidence. `kind` is the graph node type ("Image"),
    // `caption` is what a vision model said about the picture (empty when no
    // model was loaded — never filled in from the filename), and `thumbnail`
    // is the inline data: URI stored on the node. All three are empty for the
    // ordinary text citation.
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
      locator: string;
      // v11.1.0: present for a multi-modal memory (see MessageProof above);
      // absent everywhere else, including in older payloads.
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

// Folder watch mode health (GET /api/ingestion/watch): per-watch scan results
// so the home surface can show whether "connected" folders actually flow.
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
