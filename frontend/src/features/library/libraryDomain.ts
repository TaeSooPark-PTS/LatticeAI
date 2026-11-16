import { t, type Language } from "@/i18n";
import { asArray, humanizeModelId } from "@/lib/utils";

export const MODEL_STATUS_KEYS = new Set([
  "loaded", "ready", "download_required", "unavailable", "unsupported",
]);

export function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

/** Say what the search engine can do, not which provider is wired in. */
export function embeddingStateLabel(state: unknown, language: Language) {
  const value = String(state || "").toLowerCase();
  if (value === "production") return t(language, "library.embedding.state.production");
  if (value === "fallback" || value === "hash" || value === "local") {
    return t(language, "library.embedding.state.fallback");
  }
  return t(language, "library.embedding.state.unknown");
}

/** Name the machine the way its owner would, not the way `uname` does. */
export function describeComputer(profile: Record<string, unknown> | undefined, language: Language) {
  const os = String(profile?.os || "").toLowerCase();
  const arch = String(profile?.arch || "").toLowerCase();
  if (!os) return t(language, "library.value.detected");
  if (os === "darwin" && arch.startsWith("arm")) return t(language, "library.runtime.appleSilicon");
  if (os === "darwin") return "Mac";
  if (os === "win32" || os.startsWith("win")) return "Windows";
  if (os === "linux") return "Linux";
  return t(language, "library.runtime.thisComputer");
}

/**
 * English sentences the server never localized. A model *name* is short and
 * may legitimately be Latin script; a multi-word English sentence with no
 * Hangul in it is copy that was never translated.
 */
export function isUntranslatedProse(text: string) {
  if (/[ㄱ-힝]/u.test(text)) return false;
  return text.trim().split(/\s+/).length >= 6;
}

export function localizeModelMessage(
  message: unknown,
  language: Language,
  mode: string,
  fallbackKey = "library.model.notReady",
) {
  const text = String(message);
  if (language === "ko" && isUntranslatedProse(text)) return t(language, fallbackKey);
  if (mode !== "basic") return text;
  const localized = text
    .replace(/gemma4_unified/gi, t(language, "library.model.localFormat"))
    .replace(/mlx[-_ ]?vlm|mlx[-_ ]?lm|local_mlx|\bmlx\b|\bgguf\b|\bollama\b|hugging face/gi, t(language, "library.model.localSupport"))
    .replace(/runtime/gi, t(language, "library.model.support"))
    .replace(/model_type/gi, t(language, "library.model.format"));
  return language === "en"
    ? localized
      .replace(/this local model format local model support format/gi, t(language, "library.model.localFormat"))
      .replace(/local model support model support/gi, t(language, "library.model.localSupport"))
    : localized;
}

export function catalogRank(
  model: Record<string, unknown>,
  loadedIds: string[],
  currentId: string,
) {
  const id = String(model.id || model.model_id || "");
  if (model.category === "embedding" || model.role === "embedding" || model.task === "embedding") return 4;
  if (id && (loadedIds.includes(id) || id === currentId)) return 0;
  if (model.load_available || model.load_status === "ready") return 1;
  if (model.pulled) return 2;
  return 3;
}

export function deriveActiveModelView(modelsData: unknown) {
  const payload = asRecord(modelsData);
  const loadedIds = asArray<string>(payload.loaded);
  const currentId = String(payload.current || loadedIds[0] || "");
  const catalog = [
    ...asArray<Record<string, unknown>>(payload.catalog),
    ...asArray<Record<string, unknown>>(payload.recommended),
    ...asArray<Record<string, unknown>>(payload.models),
  ];
  const modelId = (model: Record<string, unknown>) => String(model.id || model.model_id || "");
  const activeEntry = currentId ? catalog.find((model) => modelId(model) === currentId) : undefined;
  const activeName = String(activeEntry?.name || "") || (currentId ? humanizeModelId(currentId) : "");
  const alternatives = catalog
    .filter((model) => {
      const id = modelId(model);
      if (!id || id === currentId || loadedIds.includes(id)) return false;
      if (model.download_required) return false;
      if (asRecord(model.runtime_compatibility).supported === false) return false;
      if (model.load_status === "unsupported") return false;
      return Boolean(model.load_available) || model.load_status === "ready";
    })
    .slice(0, 3);

  return { loadedIds, currentId, activeName, alternatives, modelId };
}

export function deriveModelsPanelView(
  modelsData: unknown,
  recsData: unknown,
  language: Language,
) {
  const modelsPayload = asRecord(modelsData);
  const recsPayload = asRecord(recsData);
  const recsBlock = asRecord(recsPayload.recommendations);
  const loadedIds = asArray<string>(modelsPayload.loaded);
  const currentId = String(modelsPayload.current || "");

  const catalog = [
    ...asArray<Record<string, unknown>>(modelsPayload.catalog),
    ...asArray<Record<string, unknown>>(modelsPayload.recommended),
  ]
    .map((model, index) => ({ model, index }))
    .sort((a, b) => catalogRank(a.model, loadedIds, currentId) - catalogRank(b.model, loadedIds, currentId) || a.index - b.index)
    .map((entry) => entry.model);

  const recommendationRows = asArray<Record<string, unknown>>(recsBlock.models);
  const recommendationById = new Map(recommendationRows.map((item) => [String(item.id), item]));
  const current = catalog.find((model) => {
    const id = String(model.id || "");
    return Boolean(id) && (loadedIds.includes(id) || id === currentId);
  });
  const topPick = (recsBlock.top_pick || null) as Record<string, unknown> | null;
  const rawEmbeddingRec = (
    recsBlock.embedding_recommendation
    || modelsPayload.embedding_recommendation
    || recommendationRows.find((item) => item.category === "embedding" || item.role === "embedding" || /embeddinggemma/i.test(String(item.id || "")))
    || null
  ) as Record<string, unknown> | null;

  const embeddingRecId = String(rawEmbeddingRec?.hf_repo_id || rawEmbeddingRec?.id || "google/embeddinggemma-2");
  const embeddingRecLoadId = String(rawEmbeddingRec?.recommended_load_id || rawEmbeddingRec?.id || "google/embeddinggemma-2");
  const embeddingRecEngine = String(rawEmbeddingRec?.recommended_engine || "local_mlx");
  const embeddingRecName = String(rawEmbeddingRec?.name || t(language, "library.embedding.recommend.title"));
  const embeddingRecSize = String(rawEmbeddingRec?.size || "0.4GB");
  const embeddingFallbackRepo = String(
    asRecord(rawEmbeddingRec?.gated_fallback).fallback_repo
      || rawEmbeddingRec?.mlx_repo_id
      || "mlx-community/embeddinggemma-2-4bit",
  );
  const embeddingLoaded = loadedIds.includes(embeddingRecId)
    || loadedIds.includes(embeddingRecLoadId)
    || currentId === embeddingRecId
    || currentId === embeddingRecLoadId;

  return {
    loadedIds,
    currentId,
    catalog,
    recommendationById,
    current,
    topPick,
    embeddingRecId,
    embeddingRecLoadId,
    embeddingRecEngine,
    embeddingRecName,
    embeddingRecSize,
    embeddingFallbackRepo,
    embeddingLoaded,
  };
}
