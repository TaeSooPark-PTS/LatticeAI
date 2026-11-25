import { asArray, clamp, humanizeModelId } from "@/lib/utils";
import {
  booleanValue,
  confidenceValue,
  dataImageValue,
  isRecord,
  numberValue,
  stringArrayValue,
  textValue,
  titleValue,
} from "./brainDataReaders";
import type {
  ApiRecord,
  BrainBrief,
  BrainDepth,
  BrainProof,
  BrainReadiness,
} from "./types";

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
        kind: textValue(item, ["kind"]),
        caption: textValue(item, ["caption"]),
        thumbnail: dataImageValue(item),
      })),
    },
    claims: {
      canRecallUserContext: booleanValue(claims, ["can_recall_user_context", "canRecallUserContext"], false),
      keepsContextAcrossModels: booleanValue(claims, ["keeps_context_across_models", "keepsContextAcrossModels"], false),
      isKnowledgeStore: booleanValue(claims, ["is_knowledge_store", "isKnowledgeStore"], false),
    },
  };
}

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

export function uniqueById<T extends { id: string }>(items: T[]) {
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

