import { asArray, humanizeModelId, isRecord as isRecordValue } from "@/lib/utils";
import { t, type Language } from "@/i18n";
import type { AgentStepEvent, ApiRecord, BrainBrief, BrainDepth, BrainProof, BrainReadiness, ConversationSummary, ExtractionQuality, IngestionEvidence, IngestionJob, IngestionWatch, IngestionWatchStatus, KnowledgeConcept, KnowledgeGraphModel, MemoryFragment, Message, MessageBrainIngest, MessageContextQuality, MessageFile, MessageGrounding, MessageLoopSummary, MessageRunExplanation, PendingApprovalSummary, RelationshipThread, VectorFreshness } from "./types";
import { clamp } from "./graphLayout";

export function buildConversationSummaries(historyData: unknown): ConversationSummary[] {
  return asArray<ApiRecord>(historyData)
    .flatMap((item): ConversationSummary[] => {
      const id = textValue(item, ["id", "conversation_id"]);
      if (!id) return [];
      // textValue falls back to the id, which the guard above proved non-blank,
      // so the trimmed title can never be empty.
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

/**
 * The memory manager labels its tiers in English ("Workspace Memory"). The id
 * is the contract, so translate on the way in and keep the server label for
 * anything we do not recognise.
 */
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

// Additive ingest meta: {"extraction_quality": {score, level, reasons}} with a
// sibling "warnings" list when the level is low. Absent → undefined (no UI).
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

// Additive chat meta: "context_quality" travels on the same channel as
// sources/evidence (the answer trace). Accepts the trailer payload or the
// trace record itself; returns null when the backend does not emit it yet.
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

// Answer-citation binding verdict from the backend ("grounding"): defensive
// parse of {status, reason}. The backend's Korean `label` field is
// intentionally ignored so display copy always comes from i18n keys.
export function parseGrounding(value: unknown): MessageGrounding | null {
  const root = isRecord(value) ? value : {};
  const direct = isRecord(root.grounding) ? root.grounding : null;
  const grounding = direct || (typeof root.status === "string" && "label" in root ? root : null);
  if (!grounding) return null;
  const status = textValue(grounding, ["status"]);
  if (!["supported", "unsupported", "no_context"].includes(status)) return null;
  const reason = typeof grounding.reason === "string" && grounding.reason.trim() ? grounding.reason.trim() : null;
  return { status, reason };
}

// "Brain remembered" chip data from a brain_ingest entry. Only ok/pending/
// failed become a chip; other statuses (skipped/unavailable/...) stay silent.
function parseBrainIngestEntry(entry: unknown): MessageBrainIngest | null {
  if (!isRecord(entry)) return null;
  const status = textValue(entry, ["status"]);
  if (status !== "ok" && status !== "pending" && status !== "failed") return null;
  const detail = textValue(entry, ["detail"]);
  return { status, ...(detail ? { detail } : {}) };
}

// Joins an agent payload's created_files with the artifacts[] preview verdict
// and the payload-level brain_ingest verdict (single dict for the one-file
// path, {path,...} list for bundles) — shared by the streaming onAgent handler
// and the approval-resume merge so both render through the same file-card path.
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

// One live `event: agent_step` frame → typed step event. Requires phase +
// event strings; every other field is optional and unknown fields are dropped
// so future backend additions never break rendering.
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

// Post-hoc timeline from the final payload's `steps` transcript, for runs that
// did not stream progress frames (approval resumes, direct file routes).
// EXECUTING entries with an action become tool events (ok unless an error is
// recorded); other entries collapse into state markers.
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

// Loop honesty meta (payload.loop): null unless something was actually
// repaired, so the "N회 보정" note only appears when it is true.
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

// Backend `explanation` payload (v9.9.6). The server already localizes into
// {ko, en}; this picks the surface language and keeps only what the note
// renders. A clean, verified run carries no details — nothing to show, so it
// stays null and the UI adds no noise.
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
  // A verified, effortless run needs no explanation at all.
  if (ok && !details.length) return null;
  return { code, ok, headline, details, strainLevel };
}

// GET /agent/approvals → pending paused runs. The token stays with the
// original approval card; these summaries only inform.
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

// GET /api/ingestion/watch → watch-mode health. Defensive: last_result and
// last_errors may be absent (older servers) — every line hides itself then.
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

// "unavailable" here is a machine state, not display copy: consumers gate on
// it (VectorFreshnessNotice only renders for "pending") or map it to i18n
// keys. Never render this status string directly in the UI.
export function parseVectorFreshness(data: unknown): VectorFreshness {
  const record = isRecord(data) ? data : {};
  return {
    status: textValue(record, ["status"], "unavailable"),
    pendingItems: Math.max(0, Math.round(numberValue(record, ["pending_items", "pendingItems"]))),
    totalItems: Math.max(0, Math.round(numberValue(record, ["total_items", "totalItems"]))),
    detail: textValue(record, ["detail"]),
  };
}

// The backend job schema records errors as {index, source, detail} objects;
// older payloads use plain strings. Normalize both into readable one-liners
// so the completion report can show skip/failure samples with reasons.
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


function catalogDisplayName(record: ApiRecord, id: string) {
  const pools = ["recommended", "cloud", "loaded", "loaded_models", "models"];
  for (const pool of pools) {
    for (const item of asArray<ApiRecord>(record[pool])) {
      const itemId = textValue(item, ["id", "model_id", "name"]);
      if (itemId !== id) continue;
      const label = textValue(item, ["display_name", "model_name", "name"]);
      if (label && label !== id) return label;
    }
  }
  return "";
}

export { humanizeModelId } from "@/lib/utils";

export function currentModelName(data: unknown) {
  const record = isRecord(data) ? data : {};
  const current = textValue(record, ["current", "current_model", "local_model"]);
  if (current) return catalogDisplayName(record, current) || humanizeModelId(current);
  const loaded = asArray<ApiRecord>(record.loaded || record.loaded_models);
  const firstLoaded = loaded.find((item) => item.id || item.name || item.model_id);
  if (!firstLoaded) return "local mind";
  const label = textValue(firstLoaded, ["display_name", "model_name", "name"]);
  const id = textValue(firstLoaded, ["id", "model_id"]);
  if (label && label !== id) return label;
  return id ? catalogDisplayName(record, id) || humanizeModelId(id) : "local mind";
}

export function hasLoadedModel(data: unknown) {
  const record = isRecord(data) ? data : {};
  if (textValue(record, ["current", "current_model", "local_model"])) return true;
  return asArray<ApiRecord>(record.loaded || record.loaded_models)
    .some((item) => item.id || item.name || item.model_id);
}

export function buildBrainReadiness(memoryData: unknown, fallbackMemoryCount: number, fallbackConceptCount: number): BrainReadiness {
  const memory = isRecord(memoryData) ? memoryData : {};
  const backend = isRecord(memory.brain_readiness) ? memory.brain_readiness : {};
  const backendState = textValue(backend, ["state"]);
  const backendDepth = numberValue(backend, ["depth"]);
  const backendScore = numberValue(backend, ["score"]);
  if ((backendState === "quiet" || backendState === "forming" || backendState === "alive") && isBrainDepth(backendDepth)) {
    const signals = isRecord(backend.signals) ? backend.signals : {};
    return {
      score: clamp(Math.round(backendScore || 0), 0, 100),
      state: backendState,
      depth: backendDepth,
      titleKey: textValue(backend, ["title_key", "titleKey"], `brain.readiness.${backendState}`),
      actionKey: textValue(backend, ["action_key", "actionKey"], readinessActionKey(backendState)),
      source: "memory_service",
      signals: {
        memoryCount: numberValue(signals, ["memory_count", "memoryCount"]),
        conceptCount: numberValue(signals, ["concept_count", "conceptCount"]),
        relationshipCount: numberValue(signals, ["relationship_count", "relationshipCount"]),
        healthySources: numberValue(signals, ["healthy_sources", "healthySources"]),
      },
    };
  }
  return fallbackBrainReadiness(fallbackMemoryCount, fallbackConceptCount);
}

export function buildBrainProof(data: unknown, fallbackModelName = ""): BrainProof {
  const result = isRecord(data) && typeof data.ok === "boolean" && "data" in data
    ? data
    : null;
  if (result && !result.ok) return unavailableBrainProof(fallbackModelName);
  const payload = result ? result.data : data;
  const proof = isRecord(payload) ? payload : {};
  const modelContinuity = isRecord(proof.model_continuity) ? proof.model_continuity : {};
  const proofs = isRecord(proof.proofs) ? proof.proofs : {};
  const recall = isRecord(proof.recall) ? proof.recall : {};
  const claims = isRecord(proof.claims) ? proof.claims : {};
  const durableItems = numberValue(proofs, ["durable_items", "durableItems"]);
  return {
    status: textValue(proof, ["status"], "quiet"),
    modelContinuity: {
      activeModel: textValue(modelContinuity, ["active_model", "activeModel"], fallbackModelName),
      brainOwner: textValue(modelContinuity, ["brain_owner", "brainOwner"], "lattice_brain"),
      capability: booleanValue(modelContinuity, ["capability"], true),
      survivesModelSwitch: booleanValue(modelContinuity, ["survives_model_switch", "survivesModelSwitch"], false),
      proven: booleanValue(modelContinuity, ["proven"], false),
      contextStore: textValue(modelContinuity, ["context_store", "contextStore"], "workspace + conversation + graph + vector"),
    },
    proofs: {
      durableItems,
      hasDurableEvidence: booleanValue(proofs, ["has_durable_evidence", "hasDurableEvidence"], durableItems > 0),
      workspaceMemories: numberValue(proofs, ["workspace_memories", "workspaceMemories"]),
      conversations: numberValue(proofs, ["conversations"]),
      graphConcepts: numberValue(proofs, ["graph_concepts", "graphConcepts"]),
      vectorItems: numberValue(proofs, ["vector_items", "vectorItems"]),
      healthySources: numberValue(proofs, ["healthy_sources", "healthySources"]),
    },
    recall: {
      query: textValue(recall, ["query"]),
      count: numberValue(recall, ["count"]),
      items: asArray<ApiRecord>(recall.items).map((item, index) => ({
        id: textValue(item, ["id"], `recall-${index}`),
        source: titleValue(item, ["source"], "Memory"),
        title: textValue(item, ["title"], "Memory"),
        snippet: textValue(item, ["snippet"]),
        score: numberValue(item, ["score"]),
        matchedTerms: stringArrayValue(item, ["matched_terms", "matchedTerms"]),
        confidence: confidenceValue(item, numberValue(item, ["score"])),
        locator: textValue(item, ["locator"]),
      })),
    },
    claims: {
      canRecallUserContext: booleanValue(claims, ["can_recall_user_context", "canRecallUserContext"], false),
      keepsContextAcrossModels: booleanValue(claims, ["keeps_context_across_models", "keepsContextAcrossModels"], false),
      isKnowledgeStore: booleanValue(claims, ["is_knowledge_store", "isKnowledgeStore"], false),
    },
  };
}

// status "unavailable" is a machine state consumed by boolean/i18n-key logic
// (useBrainProof, HomePanels). It must not be rendered as user-facing text.
function unavailableBrainProof(fallbackModelName: string): BrainProof {
  return {
    status: "unavailable",
    modelContinuity: {
      activeModel: fallbackModelName,
      brainOwner: "",
      capability: false,
      survivesModelSwitch: false,
      proven: false,
      contextStore: "",
    },
    proofs: {
      durableItems: 0,
      hasDurableEvidence: false,
      workspaceMemories: 0,
      conversations: 0,
      graphConcepts: 0,
      vectorItems: 0,
      healthySources: 0,
    },
    recall: { query: "", count: 0, items: [] },
    claims: {
      canRecallUserContext: false,
      keepsContextAcrossModels: false,
      isKnowledgeStore: false,
    },
  };
}

export function buildBrainBrief(data: unknown): BrainBrief {
  const brief = isRecord(data) ? data : {};
  const focus = isRecord(brief.focus) ? brief.focus : {};
  const rawActions = asArray<ApiRecord>(brief.next_actions || brief.nextActions);
  const rawQuestions = asArray<ApiRecord>(brief.suggested_questions || brief.suggestedQuestions);
  const rawProactive = asArray<ApiRecord>(brief.proactive_actions || brief.proactiveActions);
  const rawEvidence = asArray<ApiRecord>(brief.evidence);
  const actionRows = rawActions.length
    ? rawActions
    : [
        { id: "add_source", label_key: "brain.brief.action.add", detail_key: "brain.brief.action.add.detail", route: "/capture", priority: 10 },
        { id: "ask_brain", label_key: "brain.brief.action.ask", detail_key: "brain.brief.action.ask.detail", route: "", priority: 9 },
      ];
  const evidenceRows = rawEvidence.length
    ? rawEvidence
    : [
        { id: "durable", label_key: "brain.brief.evidence.durable", value: 0, detail_key: "brain.brief.evidence.durable.detail" },
        { id: "graph", label_key: "brain.brief.evidence.graph", value: 0, detail_key: "brain.brief.evidence.graph.detail" },
        { id: "sources", label_key: "brain.brief.evidence.sources", value: 0, detail_key: "brain.brief.evidence.sources.detail" },
      ];
  return {
    status: textValue(brief, ["status"], "quiet"),
    score: clamp(Math.round(numberValue(brief, ["score"])), 0, 100),
    headlineKey: textValue(brief, ["headline_key", "headlineKey"], "brain.brief.headline.quiet"),
    bodyKey: textValue(brief, ["body_key", "bodyKey"], "brain.brief.body.quiet"),
    focus: {
      kind: textValue(focus, ["kind"], "empty"),
      title: textValue(focus, ["title"]),
      detail: textValue(focus, ["detail"]),
      source: titleValue(focus, ["source"], "Memory"),
      score: numberValue(focus, ["score"]),
      empty: booleanValue(focus, ["empty"], !textValue(focus, ["title"])),
    },
    nextActions: actionRows.map((item) => ({
      id: textValue(item, ["id"], "ask_brain"),
      labelKey: textValue(item, ["label_key", "labelKey"], "brain.brief.action.ask"),
      detailKey: textValue(item, ["detail_key", "detailKey"], "brain.brief.action.ask.detail"),
      route: textValue(item, ["route"]),
      priority: numberValue(item, ["priority"]),
    })),
    suggestedQuestions: rawQuestions.map((item) => ({
      id: textValue(item, ["id"], "suggested-question"),
      labelKey: textValue(item, ["label_key", "labelKey"], "brain.suggestion.focus.label"),
      detailKey: textValue(item, ["detail_key", "detailKey"], "brain.suggestion.focus.detail"),
      promptKey: textValue(item, ["prompt_key", "promptKey"], "brain.suggestion.focus.prompt"),
      params: paramsValue(item.params),
      priority: numberValue(item, ["priority"]),
    })).sort((left, right) => right.priority - left.priority),
    proactiveActions: rawProactive.map((item) => ({
      id: textValue(item, ["id"], "proactive-action"),
      intent: textValue(item, ["intent"], "ask"),
      labelKey: textValue(item, ["label_key", "labelKey"], "brain.proactive.evidence.label"),
      detailKey: textValue(item, ["detail_key", "detailKey"], "brain.proactive.evidence.detail"),
      prompt: textValue(item, ["prompt"]),
      route: textValue(item, ["route"]),
      priority: numberValue(item, ["priority"]),
      context: paramsValue(item.context),
    })).sort((left, right) => right.priority - left.priority),
    evidence: evidenceRows.map((item) => ({
      id: textValue(item, ["id"], "evidence"),
      labelKey: textValue(item, ["label_key", "labelKey"], "brain.brief.evidence.durable"),
      value: numberValue(item, ["value"]),
      detailKey: textValue(item, ["detail_key", "detailKey"], "brain.brief.evidence.durable.detail"),
    })),
    generatedAt: textValue(brief, ["generated_at", "generatedAt"]),
  };
}

function uniqueById<T extends { id: string }>(items: T[]) {
  const seen = new Set<string>();
  return items.filter((item) => {
    if (seen.has(item.id)) return false;
    seen.add(item.id);
    return true;
  });
}

function paramsValue(value: unknown): Record<string, string | number> {
  if (!isRecord(value)) return {};
  return Object.entries(value).reduce<Record<string, string | number>>((params, [key, item]) => {
    if (typeof item === "string" || typeof item === "number") params[key] = item;
    return params;
  }, {});
}

function fallbackBrainReadiness(memoryCount: number, conceptCount: number): BrainReadiness {
  const score = Math.min(100, Math.round(memoryCount * 12 + conceptCount * 10));
  if (memoryCount < 1) {
    return {
      score: Math.max(12, score),
      state: "quiet",
      depth: 2,
      titleKey: "brain.readiness.quiet",
      actionKey: "brain.readiness.start",
      source: "frontend_fallback",
      signals: { memoryCount, conceptCount, relationshipCount: 0, healthySources: 0 },
    };
  }
  if (conceptCount < 3) {
    return {
      score: Math.max(38, score),
      state: "forming",
      depth: 3,
      titleKey: "brain.readiness.forming",
      actionKey: "brain.readiness.grow",
      source: "frontend_fallback",
      signals: { memoryCount, conceptCount, relationshipCount: 0, healthySources: 0 },
    };
  }
  return {
    score: Math.max(72, score),
    state: "alive",
    depth: 5,
    titleKey: "brain.readiness.alive",
    actionKey: "brain.readiness.map",
    source: "frontend_fallback",
    signals: { memoryCount, conceptCount, relationshipCount: 0, healthySources: 0 },
  };
}

function readinessActionKey(state: "quiet" | "forming" | "alive") {
  if (state === "quiet") return "brain.readiness.start";
  if (state === "forming") return "brain.readiness.grow";
  return "brain.readiness.map";
}

function isBrainDepth(value: number): value is BrainDepth {
  return value >= 1 && value <= 5 && Number.isInteger(value);
}

export const isRecord = isRecordValue as (value: unknown) => value is ApiRecord;

export function textValue(record: ApiRecord, keys: string[], fallback = "") {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value;
    if (typeof value === "number" && Number.isFinite(value)) return String(value);
  }
  return fallback;
}

function titleValue(record: ApiRecord, keys: string[], fallback = "") {
  const value = textValue(record, keys, fallback);
  return value
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

// Parse a created/added timestamp into unix epoch milliseconds. Accepts ISO 8601
// strings, unix seconds, or unix milliseconds. Returns undefined when absent or
// unparseable so the time-exploration UI can fall back gracefully.
function timestampValue(record: ApiRecord, keys: string[]): number | undefined {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "number" && Number.isFinite(value)) {
      // Heuristic: values below ~1e12 are seconds, otherwise milliseconds.
      return value < 1e12 ? value * 1000 : value;
    }
    if (typeof value === "string" && value.trim()) {
      const numeric = Number(value);
      if (Number.isFinite(numeric) && /^\d+$/.test(value.trim())) {
        return numeric < 1e12 ? numeric * 1000 : numeric;
      }
      const parsed = Date.parse(value);
      if (Number.isFinite(parsed)) return parsed;
    }
  }
  return undefined;
}

function stringArrayValue(record: ApiRecord, keys: string[]): string[] {
  for (const key of keys) {
    const value = record[key];
    if (Array.isArray(value)) {
      return value.filter((item): item is string => typeof item === "string" && item.trim().length > 0);
    }
  }
  return [];
}

// Prefer the backend's confidence band; fall back to the same score bands the
// backend uses so older responses still explain themselves.
function confidenceValue(record: ApiRecord, score: number): "high" | "medium" | "low" {
  const value = record["confidence"];
  if (value === "high" || value === "medium" || value === "low") return value;
  return score >= 0.65 ? "high" : score >= 0.3 ? "medium" : "low";
}

function numberValue(record: ApiRecord, keys: string[]) {
  for (const key of keys) {
    const value = Number(record[key]);
    if (Number.isFinite(value)) return value;
  }
  return 0;
}

function booleanValue(record: ApiRecord, keys: string[], fallback: boolean) {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "boolean") return value;
  }
  return fallback;
}
