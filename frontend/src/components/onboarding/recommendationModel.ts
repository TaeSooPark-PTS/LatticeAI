import { asArray } from "@/lib/utils";

export type FlowStep = "login" | "analysis" | "recommend" | "install";
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
  role: "best" | "faster" | "advanced";
  reason: string;
  supported: boolean;
  downloadRequired: boolean;
  downloadSize: string;
  storageLocation: string;
  externalHost: string;
  /** Estimated download time in minutes. 0 when nothing needs downloading; null when size is unknown. */
  estimatedDownloadMinutes: number | null;
  /** Estimated seconds to first response after the model is loaded. */
  estimatedFirstResponseSeconds: number;
  /** Rough parameter scale (e.g. 8, 12, 70) parsed from the model name; null when unknown. */
  parameterBillions: number | null;
};

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
  const supported = all.filter((item) => item.supported);
  const pool = supported.length ? supported : all;
  const byName = (pattern: RegExp) => pool.find((item) => pattern.test(`${item.name} ${item.id}`));
  const byId = (id?: unknown) => pool.find((item) => item.id === String(id));
  const best = byId(topPick.id) || byName(/gemma.*12|12b/i) || pool[0];
  const faster = pool.find((item) => item.id !== best?.id && /qwen|8b|7b/i.test(`${item.name} ${item.id}`)) || pool.find((item) => item.id !== best?.id);
  const advanced = pool.find((item) => item.id !== best?.id && item.id !== faster?.id && /26b|32b|70b|advanced/i.test(`${item.name} ${item.id}`))
    || pool.find((item) => item.id !== best?.id && item.id !== faster?.id);
  return [
    best ? { ...best, role: "best" as const, reason: best.reason || "best" } : null,
    faster ? { ...faster, role: "faster" as const, reason: faster.reason || "faster" } : null,
    advanced ? { ...advanced, role: "advanced" as const, reason: advanced.reason || "advanced" } : null,
  ].filter(Boolean) as RecommendedModel[];
}

export function fallbackModel(): RecommendedModel {
  return {
    id: "mlx-community/Qwen3-VL-8B-Instruct-4bit",
    loadId: "mlx-community/Qwen3-VL-8B-Instruct-4bit",
    engine: "local_mlx",
    name: "Qwen3-VL 8B",
    shortName: "Qwen 3",
    family: "Qwen 3",
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
    parameterBillions: 8,
  };
}

function toRecommendedModel(row: ApiData): RecommendedModel {
  const compatibility = asRecord(row.runtime_compatibility);
  const id = String(row.id || row.model_id || row.recommended_load_id || "");
  const loadId = String(row.recommended_load_id || row.load_id || id);
  const name = String(row.display_name || row.name || id || "recommended_brain");
  const supported = row.load_status !== "unsupported"
    && row.load_status !== "runtime_update_needed"
    && row.status !== "not_recommended"
    && compatibility.supported !== false;
  const downloadRequired = Boolean(row.download_required);
  const downloadSize = String(row.download_size || row.size || "");
  const parameterBillions = parseParameterBillions(`${name} ${id}`);
  return {
    id,
    loadId,
    engine: String(row.recommended_engine || row.engine || "local_mlx"),
    name,
    shortName: friendlyModelName(name || id),
    family: friendlyModelName(String(row.family || name || "local_brain")),
    size: String(row.size || ""),
    role: "best",
    reason: String(row.reason || ""),
    supported,
    downloadRequired,
    downloadSize,
    storageLocation: String(row.storage_location || row.local_path || "~/.latticeai/models"),
    externalHost: externalHostLabel(row),
    estimatedDownloadMinutes: downloadRequired ? estimateDownloadMinutes(downloadSize) : 0,
    estimatedFirstResponseSeconds: estimateFirstResponseSeconds(parameterBillions),
    parameterBillions,
  };
}

/** Parse a download size string like "8GB", "8.5 GB", "512MB" into megabytes; null when unparseable. */
export function parseDownloadMegabytes(value: string): number | null {
  const match = String(value || "").match(/([\d.]+)\s*(t|g|m)?b?/i);
  if (!match) return null;
  const amount = Number(match[1]);
  if (!Number.isFinite(amount) || amount <= 0) return null;
  const unit = (match[2] || "g").toLowerCase();
  if (unit === "t") return amount * 1024 * 1024;
  if (unit === "m") return amount;
  return amount * 1024; // gigabytes (default assumption for model weights)
}

/**
 * Estimate download minutes from a size string, assuming a conservative ~15 Mbps
 * (≈1.875 MB/s) average home connection. Returns null when the size is unknown so
 * the UI can fall back to a "time unknown" message instead of showing 0.
 */
export function estimateDownloadMinutes(downloadSize: string): number | null {
  const megabytes = parseDownloadMegabytes(downloadSize);
  if (megabytes === null) return null;
  const megabytesPerSecond = 15 / 8; // 15 Mbps
  const minutes = megabytes / megabytesPerSecond / 60;
  return Math.max(1, Math.round(minutes));
}

/** Pull a rough parameter scale (billions) out of a model name like "Gemma 12B" or "qwen-8b". */
export function parseParameterBillions(text: string): number | null {
  const match = String(text || "").match(/(\d{1,3}(?:\.\d)?)\s*b\b/i);
  if (!match) return null;
  const value = Number(match[1]);
  return Number.isFinite(value) && value > 0 ? value : null;
}

/** Estimate seconds to first response from model scale: 8B→5s, 12B→10s, 70B→30s. */
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
  if (/huggingface|hf\\.co|mlx-community/i.test(raw)) return "huggingface";
  if (/ollama/i.test(raw)) return "ollama";
  return raw.replace(/^https?:\/\//, "").split("/")[0] || raw;
}

export function friendlyModelName(value: string) {
  return String(value || "recommended_brain")
    .replace(/^mlx-community\//i, "")
    .replace(/[-_]?Instruct/gi, "")
    .replace(/[-_]?4bit/gi, "")
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
