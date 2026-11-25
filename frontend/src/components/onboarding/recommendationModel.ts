import { asArray } from "@/lib/utils";

export type FlowStep = "wake" | "login" | "analysis" | "recommend" | "install";
export type ApiData = Record<string, unknown>;

export type FlowAnalysis = {
  setup?: ApiData | null;
  models?: ApiData | null;
  recommendations?: ApiData | null;
  sysinfo?: ApiData | null;
};

export type RecommendedModel = {
  id: string;
  loadId: string;
  engine: string;
  name: string;
  shortName: string;
  family: string;
  size: string;
  role: "best" | "faster" | "advanced" | "embedding";
  reason: string;
  supported: boolean;
  downloadRequired: boolean;
  downloadSize: string;
  storageLocation: string;
  externalHost: string;
  estimatedDownloadMinutes: number | null;
  estimatedFirstResponseSeconds: number;
  parameterBillions: number | null;
  gatedFallback?: boolean;
  fallbackRepo?: string;
  publicMirrorRepos?: string[];
};

export type StreamMetrics = {
  percent: number;
  speedMbps: string | null;
  etaSeconds: number | null;
  bytesLabel: string | null;
  currentFile: string;
  gatedFallback: boolean;
  fallbackRepo: string;
};

export function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
  return `${Math.max(1, Math.round(bytes / (1024 * 1024)))} MB`;
}

export function formatStreamMetrics(event: ApiData | null | undefined): StreamMetrics {
  const rec = asRecord(event);
  const rawPercent = Number(rec.percent ?? 0);
  const percent = Math.max(0, Math.min(100, Math.round(rawPercent)));
  const rawSpeedBps = Number(rec.speed_bps ?? 0);
  const rawSpeedMbps = Number(rec.speed_mbps ?? (rawSpeedBps > 0 ? rawSpeedBps / (1024 * 1024) : 0));
  const speedMbps = rawSpeedMbps > 0 ? rawSpeedMbps.toFixed(1) : null;
  const rawEta = rec.eta_seconds;
  const etaSeconds = rawEta !== undefined && rawEta !== null && rawEta !== ""
    ? Math.max(0, Math.round(Number(rawEta)))
    : null;
  const downloadedBytes = Number(rec.downloaded_bytes ?? 0);
  const totalBytes = Number(rec.total_bytes ?? 0);
  const bytesLabel = downloadedBytes > 0 && totalBytes > 0
    ? `${formatBytes(downloadedBytes)} / ${formatBytes(totalBytes)}`
    : null;
  const currentFile = String(rec.current_file || rec.file || rec.detail || "");
  const gatedFallback = Boolean(rec.gated_fallback);
  const fallbackRepo = String(rec.fallback_repo || "");
  return {
    percent,
    speedMbps,
    etaSeconds,
    bytesLabel,
    currentFile,
    gatedFallback,
    fallbackRepo,
  };
}

export function buildRecommendations(analysis: FlowAnalysis | null): RecommendedModel[] {
  const models = asRecord(analysis?.models);
  const modelRows = [
    ...asArray<ApiData>(models.recommended),
    ...asArray<ApiData>(models.catalog),
  ];
  const recommendationRoot = asRecord(analysis?.recommendations?.recommendations);
  const recRows = asArray<ApiData>(recommendationRoot.models);
  const topPick = asRecord(recommendationRoot.top_pick);
  const merged = new Map<string, ApiData>();
  for (const row of [...recRows, ...modelRows]) {
    const id = String(row.id || row.model_id || row.recommended_load_id || "");
    if (!id) continue;
    merged.set(id, { ...(merged.get(id) || {}), ...row });
  }
  if (topPick.id && !merged.has(String(topPick.id))) merged.set(String(topPick.id), topPick);
  const all = Array.from(merged.values()).map(toRecommendedModel).filter((item) => item.id);
  const nonEmbeddingAll = all.filter((item) => item.role !== "embedding");
  const candidateAll = nonEmbeddingAll.length ? nonEmbeddingAll : all;
  const supported = candidateAll.filter((item) => item.supported);
  const pool = supported.length ? supported : candidateAll;
  const byName = (pattern: RegExp) => pool.find((item) => pattern.test(`${item.name} ${item.id}`));
  const byId = (id?: unknown) => pool.find((item) => item.id === String(id));
  const best = byId(topPick.id) || byName(/gemma.*12|12b/i) || pool[0];
  const bestId = best?.id;
  const faster = pool.find((item) => item.id !== bestId && /qwen|8b|7b/i.test(`${item.name} ${item.id}`)) || pool.find((item) => item.id !== bestId);
  const fasterId = faster?.id;
  const advanced = pool.find((item) => item.id !== bestId && item.id !== fasterId && /26b|27b|31b|32b|35b|70b|advanced/i.test(`${item.name} ${item.id}`))
    || pool.find((item) => item.id !== bestId && item.id !== fasterId);
  return [
    best ? { ...best, role: "best" as const, reason: best.reason || "best" } : null,
    faster ? { ...faster, role: "faster" as const, reason: faster.reason || "faster" } : null,
    advanced ? { ...advanced, role: "advanced" as const, reason: advanced.reason || "advanced" } : null,
  ].filter(Boolean) as RecommendedModel[];
}

export function recommendedEmbeddingModel(analysis: FlowAnalysis | null): RecommendedModel {
  const models = asRecord(analysis?.models);
  const recommendationRoot = asRecord(analysis?.recommendations?.recommendations);
  const setupRoot = asRecord(analysis?.setup);
  const setupRecs = asRecord(setupRoot.recommendations);
  const setupEnv = asRecord(setupRoot.environment);
  const profile = asRecord(analysis?.recommendations?.profile);

  const explicitRec = asRecord(
    recommendationRoot.embedding_recommendation
      || models.embedding_recommendation
      || setupRecs.embedding_recommendation,
  );
  if (explicitRec.id || explicitRec.model_id || explicitRec.hf_repo_id) {
    const model = toRecommendedModel({
      ...explicitRec,
      id: explicitRec.id || explicitRec.hf_repo_id || "google/embeddinggemma-2",
      category: "embedding",
      role: "embedding",
    });
    return {
      ...model,
      role: "embedding",
      reason: model.reason || "PC 맞춤 시맨틱 검색 · 지식 그래프 임베딩 모델 (Google EmbeddingGemma 2, 768차원)",
    };
  }

  const allRows = [
    ...asArray<ApiData>(recommendationRoot.models),
    ...asArray<ApiData>(models.recommended),
    ...asArray<ApiData>(models.catalog),
  ];
  const rowMatch = allRows.find((row) =>
    row.category === "embedding"
    || row.role === "embedding"
    || row.task === "embedding"
    || /embeddinggemma/i.test(String(row.id || row.name || row.hf_repo_id || "")),
  );
  if (rowMatch) {
    const model = toRecommendedModel({ ...rowMatch, category: "embedding", role: "embedding" });
    return {
      ...model,
      role: "embedding",
      reason: model.reason || "PC 맞춤 시맨틱 검색 · 지식 그래프 임베딩 모델 (Google EmbeddingGemma 2, 768차원)",
    };
  }

  const ramGb = Number(
    recommendationRoot.ram_gb
      || profile.ram_gb
      || setupEnv.ram_gb
      || 16,
  );
  const appleSilicon = Boolean(
    recommendationRoot.apple_silicon
      ?? profile.apple_silicon
      ?? (String(setupEnv.arch || "").toLowerCase().includes("arm") || Boolean(setupEnv.mlx)),
  );
  const constrained = Number.isFinite(ramGb) && ramGb > 0 && ramGb < 8;
  const id = constrained ? "google/embeddinggemma-300m" : "google/embeddinggemma-2";
  const loadId = appleSilicon
    ? (constrained ? "mlx-community/embeddinggemma-300m-4bit" : "mlx-community/embeddinggemma-2-4bit")
    : id;
  const name = constrained ? "Google EmbeddingGemma 300M" : "Google EmbeddingGemma 2";
  const size = constrained ? "0.3GB" : (appleSilicon ? "0.4GB" : "1.5GB");

  return {
    id,
    loadId,
    engine: appleSilicon ? "local_mlx" : "huggingface",
    name,
    shortName: friendlyModelName(name),
    family: "EmbeddingGemma",
    size,
    role: "embedding",
    reason: constrained
      ? `저사양 PC (${ramGb}GB RAM) 맞춤 초경량 시맨틱 임베딩 모델 (${name}, 768차원)`
      : `PC 맞춤 Hugging Face / MLX 시맨틱 임베딩 모델 (${name}, 768차원)`,
    supported: true,
    downloadRequired: false,
    downloadSize: size,
    storageLocation: "~/.latticeai/models",
    externalHost: "huggingface",
    estimatedDownloadMinutes: 1,
    estimatedFirstResponseSeconds: 2,
    parameterBillions: constrained ? 0.3 : 2,
    gatedFallback: true,
    fallbackRepo: constrained ? "mlx-community/embeddinggemma-300m-4bit" : "mlx-community/embeddinggemma-2-4bit",
    publicMirrorRepos: constrained
      ? [
          "mlx-community/embeddinggemma-300m-4bit",
          "mlx-community/embeddinggemma-300m-8bit",
          "ggml-org/embeddinggemma-300m-GGUF",
          "onnx-community/embeddinggemma-300m-ONNX",
        ]
      : [
          "mlx-community/embeddinggemma-2-4bit",
          "mlx-community/embeddinggemma-2-8bit",
          "ggml-org/embeddinggemma-2-GGUF",
          "onnx-community/embeddinggemma-300m-ONNX",
        ],
  };
}

export type AnalysisStatus = "loading" | "ready" | "unavailable";

export type AnalysisUnavailableReason = "probe_failed" | "no_supported_model";

export type AnalysisEndpointStatus = {
  setup: boolean;
  recommendations: boolean;
  models: boolean;
  sysinfo: boolean;
};

export type AnalysisOutcome = {
  status: AnalysisStatus;
  recommendations: RecommendedModel[];
  reason: AnalysisUnavailableReason | null;
  failedEndpoints: string[];
};

export function evaluateAnalysis(input: {
  analysis: FlowAnalysis | null;
  endpoints: AnalysisEndpointStatus | null;
}): AnalysisOutcome {
  const { analysis, endpoints } = input;
  if (!endpoints) {
    return { status: "loading", recommendations: [], reason: null, failedEndpoints: [] };
  }
  const failedEndpoints = (Object.keys(endpoints) as (keyof AnalysisEndpointStatus)[])
    .filter((key) => !endpoints[key]);
  const modelDataOk = endpoints.recommendations || endpoints.models;
  if (!modelDataOk) {
    return { status: "unavailable", recommendations: [], reason: "probe_failed", failedEndpoints };
  }
  const supported = buildRecommendations(analysis).filter((item) => item.supported);
  if (!supported.length) {
    return { status: "unavailable", recommendations: [], reason: "no_supported_model", failedEndpoints };
  }
  return { status: "ready", recommendations: supported, reason: null, failedEndpoints };
}

export function fallbackModel(): RecommendedModel {
  return {
    id: "mlx-community/gemma-4-e2b-it-4bit",
    loadId: "mlx-community/gemma-4-e2b-it-4bit",
    engine: "local_mlx",
    name: "Gemma 4 E2B",
    shortName: "Gemma 4",
    family: "Gemma 4",
    size: "",
    role: "best",
    reason: "best",
    supported: true,
    downloadRequired: false,
    downloadSize: "",
    storageLocation: "~/.latticeai/models",
    externalHost: "",
    estimatedDownloadMinutes: 0,
    estimatedFirstResponseSeconds: 5,
    parameterBillions: 2,
  };
}

export function toRecommendedModel(row: ApiData): RecommendedModel {
  const compatibility = asRecord(row.runtime_compatibility);
  const id = String(row.id || row.model_id || row.recommended_load_id || "");
  const loadId = String(row.recommended_load_id || row.load_id || id);
  const name = String(row.display_name || row.name || id || "recommended_brain");
  const isEmbedding = row.category === "embedding"
    || row.role === "embedding"
    || row.task === "embedding"
    || /embeddinggemma/i.test(`${name} ${id}`);
  const supported = row.load_status !== "unsupported"
    && row.load_status !== "runtime_update_needed"
    && row.status !== "not_recommended"
    && compatibility.supported !== false;
  const downloadRequired = Boolean(row.download_required);
  const downloadSize = downloadRequired ? String(row.download_size || row.size || "") : "";
  const parameterBillions = parseParameterBillions(`${name} ${id}`);
  const gatedInfo = asRecord(row.gated_fallback);
  const fallbackRepo = String(
    gatedInfo.fallback_repo
      || row.mlx_repo_id
      || row.gguf_repo_id
      || (isEmbedding ? "mlx-community/embeddinggemma-2-4bit" : ""),
  );
  const publicMirrorRepos = asArray<string>(row.public_mirror_repos);
  return {
    id,
    loadId,
    engine: String(row.recommended_engine || row.engine || "local_mlx"),
    name,
    shortName: friendlyModelName(name),
    family: friendlyModelName(String(row.family || name)),
    size: String(row.size || ""),
    role: isEmbedding ? "embedding" : "best",
    reason: String(row.reason || ""),
    supported,
    downloadRequired,
    downloadSize,
    storageLocation: String(row.storage_location || row.local_path || "~/.latticeai/models"),
    externalHost: downloadRequired ? externalHostLabel(row) : "",
    estimatedDownloadMinutes: downloadRequired ? estimateDownloadMinutes(downloadSize) : 0,
    estimatedFirstResponseSeconds: estimateFirstResponseSeconds(parameterBillions),
    parameterBillions,
    gatedFallback: Boolean(gatedInfo.active ?? isEmbedding),
    fallbackRepo,
    publicMirrorRepos,
  };
}

export function parseDownloadMegabytes(value: string): number | null {
  const match = String(value || "").match(/([\d.]+)\s*(t|g|m)?b?/i);
  if (!match) return null;
  const amount = Number(match[1]);
  if (!Number.isFinite(amount) || amount <= 0) return null;
  const unit = (match[2] || "g").toLowerCase();
  if (unit === "t") return amount * 1024 * 1024;
  if (unit === "m") return amount;
  return amount * 1024;
}

export function estimateDownloadMinutes(downloadSize: string): number | null {
  const megabytes = parseDownloadMegabytes(downloadSize);
  if (megabytes === null) return null;
  const megabytesPerSecond = 15 / 8;
  const minutes = megabytes / megabytesPerSecond / 60;
  return Math.max(1, Math.round(minutes));
}

export function parseParameterBillions(text: string): number | null {
  const match = String(text || "").match(/(\d{1,3}(?:\.\d)?)\s*b\b/i);
  if (!match) return null;
  const value = Number(match[1]);
  return value > 0 ? value : null;
}

export function estimateFirstResponseSeconds(parameterBillions: number | null): number {
  if (parameterBillions === null) return 8;
  if (parameterBillions <= 9) return 5;
  if (parameterBillions <= 16) return 10;
  if (parameterBillions <= 40) return 18;
  return 30;
}

function externalHostLabel(row: ApiData) {
  const raw = String(row.source_url || row.download_url || row.repository || row.provider || row.id || "");
  if (!raw) return "";
  if (/huggingface|hf\.co|mlx-community|google\/embeddinggemma/i.test(raw)) return "huggingface";
  if (/ollama/i.test(raw)) return "ollama";
  return raw.replace(/^https?:\/\//, "").split("/")[0] || raw;
}

export function friendlyModelName(value: string) {
  return String(value || "recommended_brain")
    .replace(/^mlx-community\//i, "")
    .replace(/^google\//i, "")
    .replace(/[-_]?Instruct/gi, "")
    .replace(/[-_]?4bit/gi, "")
    .replace(/embeddinggemma[-_ ]?2\b/gi, "EmbeddingGemma 2")
    .replace(/embeddinggemma[-_ ]?300m\b/gi, "EmbeddingGemma 300M")
    .replace(/^Google\s+EmbeddingGemma/i, "EmbeddingGemma")
    .replace(/Qwen3[-_ ]?VL/gi, "Qwen 3")
    .replace(/Qwen3/gi, "Qwen 3")
    .replace(/Gemma[-_ ]?4/gi, "Gemma 4")
    .replace(/A4B/gi, "")
    .replace(/[-_]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export function asRecord(value: unknown): ApiData {
  return value && typeof value === "object" && !Array.isArray(value) ? value as ApiData : {};
}
