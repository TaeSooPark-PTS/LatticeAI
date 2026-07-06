import { asArray } from "@/lib/utils";
import type { ApiRecord, BrainBrief, BrainDepth, BrainProof, BrainReadiness, ConversationSummary, KnowledgeConcept, KnowledgeGraphModel, MemoryFragment, Message, RelationshipThread } from "./types";
import { clamp } from "./graphLayout";

export function buildConversationSummaries(historyData: unknown): ConversationSummary[] {
  return asArray<ApiRecord>(historyData)
    .flatMap((item): ConversationSummary[] => {
      const id = textValue(item, ["id", "conversation_id"]);
      if (!id) return [];
      const title = textValue(item, ["title", "summary", "last_message"], id).trim() || id;
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

export function buildMemoryFragments(memoryData: unknown, historyData: unknown): MemoryFragment[] {
  const memory = isRecord(memoryData) ? memoryData : {};
  const sourceRows = asArray<ApiRecord>(memory.sources).length
    ? asArray<ApiRecord>(memory.sources)
    : asArray<ApiRecord>(memory.tiers);
  const sourceFragments = sourceRows.map((item, index) => ({
    id: textValue(item, ["id", "source", "label"], `memory-${index}`),
    title: textValue(item, ["title", "label", "source", "path", "name"], "Workspace memory"),
    kind: titleValue(item, ["type", "source_type", "kind", "health"], "Memory"),
  }));
  const conversationFragments = asArray<ApiRecord>(historyData).map((item, index) => ({
    id: textValue(item, ["id", "conversation_id"], `conversation-${index}`),
    title: textValue(item, ["title", "summary", "id"], "Conversation"),
    kind: "Conversation",
  }));

  return uniqueById([...sourceFragments, ...conversationFragments]).slice(0, 10);
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

export function currentModelName(data: unknown) {
  const record = isRecord(data) ? data : {};
  const current = textValue(record, ["current", "current_model", "local_model"]);
  if (current) return current;
  const loaded = asArray<ApiRecord>(record.loaded || record.loaded_models);
  const firstLoaded = loaded.find((item) => item.id || item.name || item.model_id);
  return firstLoaded ? textValue(firstLoaded, ["name", "id", "model_id"], "local mind") : "local mind";
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
  const proof = isRecord(data) ? data : {};
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
      })),
    },
    claims: {
      canRecallUserContext: booleanValue(claims, ["can_recall_user_context", "canRecallUserContext"], false),
      keepsContextAcrossModels: booleanValue(claims, ["keeps_context_across_models", "keepsContextAcrossModels"], false),
      isKnowledgeStore: booleanValue(claims, ["is_knowledge_store", "isKnowledgeStore"], false),
    },
  };
}

export function buildBrainBrief(data: unknown): BrainBrief {
  const brief = isRecord(data) ? data : {};
  const focus = isRecord(brief.focus) ? brief.focus : {};
  const rawActions = asArray<ApiRecord>(brief.next_actions || brief.nextActions);
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

export function isRecord(value: unknown): value is ApiRecord {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

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
