import { asArray } from "@/lib/utils";
import type { ApiRecord, BrainDepth, BrainReadiness, KnowledgeConcept, KnowledgeGraphModel, MemoryFragment, RelationshipThread } from "./types";
import { clamp } from "./graphLayout";

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
    return [{ id, label, type, summary, importance }];
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

function numberValue(record: ApiRecord, keys: string[]) {
  for (const key of keys) {
    const value = Number(record[key]);
    if (Number.isFinite(value)) return value;
  }
  return 0;
}
