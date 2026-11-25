import { t, type Language } from "@/i18n";
import { asArray, clamp } from "@/lib/utils";
import {
  booleanValue,
  isRecord,
  numberValue,
  stringArrayValue,
  textValue,
  timestampValue,
  titleValue,
} from "./brainDataReaders";
import { uniqueById } from "./brainDataReadiness";
export { isRecord, textValue } from "./brainDataReaders";
export {
  buildBrainBrief,
  buildBrainProof,
  buildBrainReadiness,
  currentModelName,
  hasLoadedModel,
  humanizeModelId,
} from "./brainDataReadiness";
import type {
  AgentStepEvent,
  ApiRecord,
  ConversationSummary,
  ExtractionQuality,
  IngestionEvidence,
  IngestionJob,
  IngestionWatch,
  IngestionWatchStatus,
  KnowledgeConcept,
  KnowledgeGraphModel,
  MemoryFragment,
  Message,
  MessageBrainIngest,
  MessageCloudAnswer,
  MessageContextQuality,
  MessageFile,
  MessageGrounding,
  MessageHybridContext,
  MessageKgExpansion,
  MessageLoopSummary,
  MessageRunExplanation,
  PendingApprovalSummary,
  RelationshipThread,
  VectorFreshness,
} from "./types";

export function buildConversationSummaries(historyData: unknown): ConversationSummary[] {
  return asArray<ApiRecord>(historyData)
    .flatMap((item): ConversationSummary[] => {
      const id = textValue(item, ["id", "conversation_id"]);
      if (!id) return [];
      const title = textValue(item, ["title", "summary", "last_message"], id).trim();
      return [{
        id,
        title,
        messageCount: Math.max(0, Math.round(numberValue(item, ["message_count", "messages"]))),
        updatedAt: timestampValue(item, ["updated_at", "updatedAt", "created_at", "createdAt"]),
      }];
    })
    .sort((left, right) => (right.updatedAt ?? 0) - (left.updatedAt ?? 0));
}

export function parseConversationMessages(data: unknown): Message[] {
  const record = isRecord(data) ? data : {};
  return asArray<ApiRecord>(record.messages).flatMap((item): Message[] => {
    const role = textValue(item, ["role"]);
    const content = typeof item.content === "string" ? item.content : "";
    if ((role !== "user" && role !== "assistant") || !content.trim()) return [];
    return [{ role, content }];
  });
}

function memoryTierTitle(item: ApiRecord, language: Language, fallback: string) {
  const id = textValue(item, ["id"]);
  if (id) {
    const key = `brain.memoryTier.${id}`;
    const label = t(language, key);
    if (label !== key) return label;
  }
  return fallback;
}

export function buildMemoryFragments(memoryData: unknown, historyData: unknown, language: Language = "ko"): MemoryFragment[] {
  const memory = isRecord(memoryData) ? memoryData : {};
  const sourceRows = asArray<ApiRecord>(memory.sources).length
    ? asArray<ApiRecord>(memory.sources)
    : asArray<ApiRecord>(memory.tiers);
  const sourceFragments = sourceRows.map((item, index) => ({
    id: textValue(item, ["id", "source", "label"], `memory-${index}`),
    title: memoryTierTitle(item, language, textValue(item, ["title", "label", "source", "path", "name"], "Workspace memory")),
    kind: titleValue(item, ["type", "source_type", "kind", "health"], "Memory"),
    tags: [],
    agentGenerated: false,
  }));
  const recentFragments = asArray<ApiRecord>(memory.recent_memories || memory.recentMemories).map((item, index) => {
    const metadata = isRecord(item.metadata) ? item.metadata : {};
    const tags = stringArrayValue(item, ["tags"]);
    const content = textValue(item, ["content", "summary", "detail"]);
    const source = textValue(metadata, ["source"]);
    const agentGenerated = tags.includes("agent-synthesis") || source === "agent_runtime" || source === "agent_runtime_synthesis";
    return {
      id: textValue(item, ["id"], `recent-memory-${index}`),
      title: content ? content.replace(/\s+/g, " ").slice(0, 96) : titleValue(item, ["kind"], "Memory"),
      kind: titleValue(item, ["kind"], "Memory"),
      detail: content,
      tags,
      agentGenerated,
    };
  });
  const conversationFragments = asArray<ApiRecord>(historyData).map((item, index) => ({
    id: textValue(item, ["id", "conversation_id"], `conversation-${index}`),
    title: textValue(item, ["title", "summary", "id"], "Conversation"),
    kind: "Conversation",
    tags: [],
    agentGenerated: false,
  }));

  return uniqueById([...recentFragments, ...sourceFragments, ...conversationFragments]).slice(0, 12);
}

export function parseKnowledgeGraph(data: unknown): KnowledgeGraphModel {
  const graph = isRecord(data) ? data : {};
  const rawNodes = asArray<ApiRecord>(graph.nodes);
  const rawEdges = asArray<ApiRecord>(graph.edges);
  const nodes = rawNodes.flatMap((node): KnowledgeConcept[] => {
    const id = textValue(node, ["id", "node_id", "title", "label"]);
    if (!id) return [];
    const metadata = isRecord(node.metadata) ? node.metadata : {};
    const type = titleValue(node, ["type", "kind", "category"], "Concept");
    const label = textValue(node, ["title", "label", "name"], id.replace(/^[^:]+:/, ""));
    const summary = textValue(node, ["summary", "description", "snippet"]) || textValue(metadata, ["summary", "description", "relative_path", "filename"]);
    const importance = clamp(numberValue(node, ["importance_norm", "importance", "score"]) || 0.5, 0.08, 1);
    const createdAt = timestampValue(node, ["created_at", "createdAt", "added_at", "addedAt", "timestamp", "updated_at", "updatedAt"])
      ?? timestampValue(metadata, ["created_at", "createdAt", "added_at", "addedAt", "timestamp", "updated_at", "updatedAt"]);
    return [{ id, label, type, summary, importance, ...(createdAt !== undefined ? { createdAt } : {}) }];
  }).sort((left, right) => right.importance - left.importance);
  const ids = new Set(nodes.map((node) => node.id));
  const edges = rawEdges.flatMap((edge, index): RelationshipThread[] => {
    const source = textValue(edge, ["from", "source", "source_id"]);
    const target = textValue(edge, ["to", "target", "target_id"]);
    if (!source || !target || !ids.has(source) || !ids.has(target)) return [];
    return [{
      id: textValue(edge, ["id"], `edge-${index}`),
      source,
      target,
      label: titleValue(edge, ["type", "label", "relationship"], "Relates"),
      weight: numberValue(edge, ["weight", "score", "confidence"]) || 1,
    }];
  });
  return { nodes, edges };
}

export function extractIngestionEvidence(data: unknown): IngestionEvidence {
  const root = isRecord(data) ? data : {};
  const nested = [root, root.knowledge_graph, root.ingestion, root.result]
    .filter(isRecord) as ApiRecord[];
  const nodeIds = new Set<string>();
  let chunkCount = 0;
  let duplicate: boolean | undefined;
  let provenanceId: string | undefined;

  for (const item of nested) {
    const nodeId = textValue(item, ["node_id", "graph_node", "graph_node_id"]);
    if (nodeId) nodeIds.add(nodeId);
    for (const indexed of asArray<unknown>(item.indexed_nodes)) {
      if (typeof indexed === "string" && indexed.trim()) nodeIds.add(indexed.trim());
      else if (isRecord(indexed)) {
        const indexedId = textValue(indexed, ["node_id", "graph_node_id", "id"]);
        if (indexedId) nodeIds.add(indexedId);
      }
    }
    chunkCount = Math.max(chunkCount, numberValue(item, ["chunk_count", "chunks", "indexed_count"]));
    if (typeof item.duplicate === "boolean") duplicate = item.duplicate;
    provenanceId ||= textValue(item, ["provenance_id"]);
  }

  let extraction: ExtractionQuality | undefined;
  for (const item of nested) {
    const parsed = parseExtractionQuality(item);
    if (parsed) {
      extraction = parsed;
      break;
    }
  }

  return {
    nodeIds: Array.from(nodeIds),
    chunkCount,
    ...(duplicate !== undefined ? { duplicate } : {}),
    ...(provenanceId ? { provenanceId } : {}),
    ...(extraction ? { extraction } : {}),
  };
}

export function parseExtractionQuality(container: unknown): ExtractionQuality | null {
  const root = isRecord(container) ? container : {};
  const quality = isRecord(root.extraction_quality) ? root.extraction_quality : null;
  if (!quality) return null;
  const level = textValue(quality, ["level"]);
  if (level !== "high" && level !== "medium" && level !== "low") return null;
  const warnings = stringArrayValue(root, ["warnings"]).length
    ? stringArrayValue(root, ["warnings"])
    : stringArrayValue(quality, ["warnings"]);
  return {
    score: numberValue(quality, ["score"]),
    level,
    reasons: stringArrayValue(quality, ["reasons"]),
    warnings,
  };
}

export function parseContextQuality(value: unknown): MessageContextQuality | null {
  const root = isRecord(value) ? value : {};
  const direct = isRecord(root.context_quality) ? root.context_quality : null;
  const nested = !direct && isRecord(root.trace) && isRecord(root.trace.context_quality)
    ? root.trace.context_quality
    : null;
  const quality = direct || nested || (typeof root.mode === "string" && typeof root.limited === "boolean" ? root : null);
  if (!quality) return null;
  const mode = textValue(quality, ["mode"]);
  if (!mode) return null;
  const reason = typeof quality.reason === "string" && quality.reason.trim() ? quality.reason.trim() : null;
  return {
    mode,
    nodes: Math.max(0, Math.round(numberValue(quality, ["nodes"]))),
    limited: booleanValue(quality, ["limited"], false),
    reason,
  };
}

export function parseGrounding(value: unknown): MessageGrounding | null {
  const root = isRecord(value) ? value : {};
  const direct = isRecord(root.grounding) ? root.grounding : null;
  const grounding = direct || (typeof root.status === "string" && "label" in root ? root : null);
  if (!grounding) return null;
  const status = textValue(grounding, ["status"]);
  if (!["supported", "unsupported", "no_context"].includes(status)) return null;
  const reason = typeof grounding.reason === "string" && grounding.reason.trim() ? grounding.reason.trim() : null;
  const cv = isRecord(grounding.claim_verification) ? grounding.claim_verification : null;
  if (!cv) return { status, reason };
  const abstentionReason =
    typeof cv.abstention_reason === "string" && cv.abstention_reason.trim() ? cv.abstention_reason.trim() : null;
  return {
    status,
    reason,
    claimVerification: {
      claimSupportRatio: clampRatio(numberValue(cv, ["claim_support_ratio"])),
      citationPrecision: clampRatio(numberValue(cv, ["citation_precision"])),
      citationRecall: clampRatio(numberValue(cv, ["citation_recall"])),
      supersededCitationLeaks: Math.max(0, Math.round(numberValue(cv, ["superseded_citation_leaks"]))),
      numericMismatches: Math.max(0, Math.round(numberValue(cv, ["numeric_mismatches"]))),
      polarityMismatches: Math.max(0, Math.round(numberValue(cv, ["polarity_mismatches"]))),
      shouldAbstain: booleanValue(cv, ["should_abstain"], false),
      abstentionReason,
    },
  };
}

function clampRatio(value: number): number {
  return Math.max(0, Math.min(1, value));
}

export function parseHybridContext(value: unknown): MessageHybridContext | null {
  if (!isRecord(value)) return null;
  const type = textValue(value, ["type"]);
  if (type && type !== "hybrid_context") return null;
  const hasNodes = Array.isArray(value.node_ids) || Array.isArray(value.nodeIds);
  if (type !== "hybrid_context" && !hasNodes) return null;
  return {
    nodeIds: stringArrayValue(value, ["node_ids", "nodeIds"]),
    keywords: stringArrayValue(value, ["keywords"]),
  };
}

export type ParsedHybridDone = {
  answer: string;
  provider: string;
  model: string;
  sentNodeIds: string[];
  expansion: MessageKgExpansion | null;
};

export function parseHybridDone(value: unknown): ParsedHybridDone | null {
  if (!isRecord(value)) return null;
  const type = textValue(value, ["type"]);
  if (type && type !== "hybrid_done") return null;
  if (
    type !== "hybrid_done"
    && !("answer" in value)
    && !("kg_expansion" in value)
    && !("kgExpansion" in value)
  ) return null;
  return {
    answer: typeof value.answer === "string" ? value.answer : "",
    provider: textValue(value, ["provider"]),
    model: textValue(value, ["model"]),
    sentNodeIds: stringArrayValue(value, ["sent_node_ids", "sentNodeIds"]),
    expansion: parseKgExpansion(value.kg_expansion ?? value.kgExpansion),
  };
}

export function parseKgExpansion(value: unknown): MessageKgExpansion | null {
  if (!isRecord(value)) return null;
  const status = textValue(value, ["status"]);
  const plan = isRecord(value.plan) ? value.plan : {};
  const provenance = isRecord(plan.provenance) ? plan.provenance : {};
  const fromField = numberValue(value, ["candidate_count", "candidateCount"]);
  const fromProvenance = numberValue(provenance, ["candidate_count", "candidateCount"]);
  const fromPlan = Array.isArray(plan.new_nodes) ? plan.new_nodes.length : 0;
  const candidateCount = Math.max(0, Math.round(fromField || fromProvenance || fromPlan));
  const stagedForReview = status === "staged"
    || status === "queued_for_review"
    || Boolean(value.review_item_id);
  return {
    status: status || "staged",
    candidateCount,
    stagedForReview,
  };
}

export function cloudAnswerFromDone(
  done: ParsedHybridDone,
  fallbackNodeCount = 0,
): MessageCloudAnswer {
  return {
    provider: done.provider,
    model: done.model,
    sentNodeCount: done.sentNodeIds.length || fallbackNodeCount,
    expansion: done.expansion,
  };
}

function parseBrainIngestEntry(entry: unknown): MessageBrainIngest | null {
  if (!isRecord(entry)) return null;
  const status = textValue(entry, ["status"]);
  if (status !== "ok" && status !== "pending" && status !== "failed") return null;
  const detail = textValue(entry, ["detail"]);
  return { status, ...(detail ? { detail } : {}) };
}

export function agentPayloadFiles(agent: {
  created_files?: Array<{ path: string; filename?: string; bytes?: number }>;
  artifacts?: Array<Record<string, unknown>>;
  generation?: { repaired?: boolean };
  brain_ingest?: Record<string, unknown> | Array<Record<string, unknown>>;
}): MessageFile[] {
  const repaired = Boolean(agent.generation?.repaired);
  const previewableByPath = new Map<string, boolean>();
  for (const artifact of agent.artifacts || []) {
    if (artifact && typeof artifact.path === "string") {
      previewableByPath.set(artifact.path, Boolean(artifact.previewable));
    }
  }
  const ingestByPath = new Map<string, MessageBrainIngest>();
  let singleIngest: MessageBrainIngest | null = null;
  if (Array.isArray(agent.brain_ingest)) {
    for (const entry of agent.brain_ingest) {
      const ingest = parseBrainIngestEntry(entry);
      if (ingest && isRecord(entry) && typeof entry.path === "string") {
        ingestByPath.set(entry.path, ingest);
      }
    }
  } else {
    singleIngest = parseBrainIngestEntry(agent.brain_ingest);
  }
  const files = agent.created_files || [];
  return files.map((file) => {
    const brainIngest = ingestByPath.get(file.path)
      || (files.length === 1 ? singleIngest : null);
    return {
      path: file.path,
      filename: file.filename || file.path.split("/").pop() || file.path,
      bytes: file.bytes || 0,
      repaired,
      ...(previewableByPath.has(file.path)
        ? { previewable: previewableByPath.get(file.path) }
        : {}),
      ...(brainIngest ? { brainIngest } : {}),
    };
  });
}

export function parseAgentStepEvent(value: unknown): AgentStepEvent | null {
  if (!isRecord(value)) return null;
  const phase = textValue(value, ["phase"]);
  const event = textValue(value, ["event"]);
  if (!phase || !event) return null;
  const step = Number(value.step);
  return {
    phase,
    event,
    ...(textValue(value, ["action"]) ? { action: textValue(value, ["action"]) } : {}),
    ...(textValue(value, ["path"]) ? { path: textValue(value, ["path"]) } : {}),
    ...(Number.isFinite(step) ? { step: Math.round(step) } : {}),
    ...(typeof value.ok === "boolean" ? { ok: value.ok } : {}),
    ...(textValue(value, ["decision"]) ? { decision: textValue(value, ["decision"]) } : {}),
    ...(textValue(value, ["verdict"]) ? { verdict: textValue(value, ["verdict"]) } : {}),
    ...(textValue(value, ["state"]) ? { state: textValue(value, ["state"]) } : {}),
    ...(textValue(value, ["detail", "error"]) ? { detail: textValue(value, ["detail", "error"]) } : {}),
  };
}

export function parseAgentTranscript(steps: unknown): AgentStepEvent[] {
  return asArray<ApiRecord>(steps).flatMap((item): AgentStepEvent[] => {
    if (!isRecord(item)) return [];
    const action = textValue(item, ["action", "tool"]);
    const state = textValue(item, ["state"]);
    const error = textValue(item, ["error"]);
    if (!action && !state && !error) return [];
    const args = isRecord(item.args) ? item.args : {};
    const path = textValue(args, ["path", "target", "file"]);
    if (action) {
      return [{
        phase: "execute",
        event: "tool",
        action,
        ...(path ? { path } : {}),
        ok: !error,
        ...(error ? { detail: error } : {}),
      }];
    }
    if (error) {
      return [{ phase: "execute", event: "state", ...(state ? { state } : {}), ok: false, detail: error }];
    }
    return [{ phase: "terminal", event: "state", state }];
  });
}

export function parseLoopSummary(value: unknown): MessageLoopSummary | null {
  if (!isRecord(value)) return null;
  const repairs: Record<string, number> = {};
  if (isRecord(value.repairs)) {
    for (const [kind, raw] of Object.entries(value.repairs)) {
      const count = Number(raw);
      if (Number.isFinite(count) && count > 0) repairs[kind] = Math.round(count);
    }
  }
  const parseErrors = Math.max(0, Math.round(numberValue(value, ["parse_errors", "parseErrors"])));
  const parseRecovered = Math.max(0, Math.round(numberValue(value, ["parse_recovered", "parseRecovered"])));
  const total = Object.values(repairs).reduce((sum, count) => sum + count, 0) + parseRecovered;
  if (total < 1) return null;
  return { repairs, parseErrors, parseRecovered, total };
}

export function parseRunExplanation(
  value: unknown,
  language: "ko" | "en",
): MessageRunExplanation | null {
  if (!isRecord(value)) return null;
  const pick = (entry: unknown): string => {
    if (!isRecord(entry)) return "";
    const text = entry[language];
    return typeof text === "string" ? text : "";
  };
  const headline = pick(value.headline);
  const details = asArray<unknown>(value.details).map(pick).filter(Boolean);
  const code = textValue(value, ["code"]);
  if (!headline && !details.length) return null;
  const strain = isRecord(value.model_strain) ? textValue(value.model_strain, ["level"]) : "";
  const strainLevel =
    strain === "light" || strain === "moderate" || strain === "heavy" ? strain : "none";
  const ok = value.ok === true;
  if (ok && !details.length) return null;
  return { code, ok, headline, details, strainLevel };
}

export function parsePendingApprovals(data: unknown): PendingApprovalSummary[] {
  const record = isRecord(data) ? data : {};
  return asArray<ApiRecord>(record.pending).flatMap((item): PendingApprovalSummary[] => {
    const runId = textValue(item, ["run_id", "runId"]);
    if (!runId) return [];
    return [{
      runId,
      goal: textValue(item, ["goal"]),
      expiresAt: textValue(item, ["expires_at", "expiresAt"]),
    }];
  });
}

export function parseIngestionWatchStatus(data: unknown): IngestionWatchStatus {
  const record = isRecord(data) ? data : {};
  const watches = asArray<ApiRecord>(record.watches).flatMap((item): IngestionWatch[] => {
    if (!isRecord(item)) return [];
    const id = textValue(item, ["id"]);
    const path = textValue(item, ["path"]);
    if (!id && !path) return [];
    const lastResultRecord = isRecord(item.last_result) ? item.last_result : null;
    const lastErrors = asArray<unknown>(item.last_errors).flatMap((entry) => {
      if (typeof entry === "string" && entry.trim()) return [{ path: "", detail: entry.trim() }];
      if (!isRecord(entry)) return [];
      const detail = textValue(entry, ["detail", "reason", "error"]);
      const errorPath = textValue(entry, ["path", "source", "file"]);
      if (!detail && !errorPath) return [];
      return [{ path: errorPath, detail }];
    });
    return [{
      id: id || path,
      path,
      enabled: booleanValue(item, ["enabled"], false),
      lastScanAt: textValue(item, ["last_scan_at", "lastScanAt"]),
      lastResult: lastResultRecord
        ? {
            status: textValue(lastResultRecord, ["status"]),
            ingested: Math.max(0, Math.round(numberValue(lastResultRecord, ["ingested"]))),
            failed: Math.max(0, Math.round(numberValue(lastResultRecord, ["failed"]))),
          }
        : null,
      trackedFiles: Math.max(0, Math.round(numberValue(item, ["tracked_files", "trackedFiles"]))),
      lastErrors: lastErrors.slice(0, 3),
    }];
  });
  return {
    enabledCount: Math.max(0, Math.round(numberValue(record, ["enabled_count", "enabledCount"]))),
    polling: booleanValue(record, ["polling"], false),
    intervalSeconds: Math.max(0, Math.round(numberValue(record, ["interval_seconds", "intervalSeconds"]))),
    watches,
  };
}

export function parseVectorFreshness(data: unknown): VectorFreshness {
  const record = isRecord(data) ? data : {};
  return {
    status: textValue(record, ["status"], "unavailable"),
    pendingItems: Math.max(0, Math.round(numberValue(record, ["pending_items", "pendingItems"]))),
    totalItems: Math.max(0, Math.round(numberValue(record, ["total_items", "totalItems"]))),
    detail: textValue(record, ["detail"]),
  };
}

function jobErrorText(entry: unknown): string {
  if (typeof entry === "string") return entry.trim();
  if (!isRecord(entry)) return "";
  const source = textValue(entry, ["source", "path", "file"]);
  const detail = textValue(entry, ["detail", "reason", "error"]);
  return [source, detail].filter(Boolean).join(" — ");
}

export function parseIngestionJobs(data: unknown): IngestionJob[] {
  const record = isRecord(data) ? data : {};
  return asArray<ApiRecord>(record.jobs).flatMap((item): IngestionJob[] => {
    const jobId = textValue(item, ["job_id", "jobId", "id"]);
    if (!jobId) return [];
    const createdAt = textValue(item, ["created_at", "createdAt"]);
    const updatedAt = textValue(item, ["updated_at", "updatedAt"]);
    return [{
      jobId,
      status: textValue(item, ["status"], "queued"),
      total: Math.max(0, Math.round(numberValue(item, ["total"]))),
      processed: Math.max(0, Math.round(numberValue(item, ["processed"]))),
      failed: Math.max(0, Math.round(numberValue(item, ["failed"]))),
      errors: asArray<unknown>(item.errors).map(jobErrorText).filter(Boolean).slice(0, 10),
      ...(createdAt ? { createdAt } : {}),
      ...(updatedAt ? { updatedAt } : {}),
    }];
  });
}


