import { describe, expect, it } from "vitest";
import {
  asRecord,
  buildRecommendations,
  estimateDownloadMinutes,
  estimateFirstResponseSeconds,
  evaluateAnalysis,
  fallbackModel,
  friendlyModelName,
  parseDownloadMegabytes,
  parseParameterBillions,
  toRecommendedModel,
  type AnalysisEndpointStatus,
  type FlowAnalysis,
} from "./recommendationModel";

const allFailed: AnalysisEndpointStatus = { setup: false, recommendations: false, models: false, sysinfo: false };
const allOk: AnalysisEndpointStatus = { setup: true, recommendations: true, models: true, sysinfo: true };

function analysisWithModels(models: Record<string, unknown>[]): FlowAnalysis {
  return {
    setup: { environment: { arch: "arm64" } },
    recommendations: { recommendations: { models } },
    models: { recommended: models, catalog: [], loaded: [] },
    sysinfo: {},
  };
}

const supportedRow = {
  id: "mlx-community/Gemma-3-12B-Instruct-4bit",
  display_name: "Gemma 3 12B",
  recommended_engine: "local_mlx",
  load_status: "ready",
  runtime_compatibility: { supported: true },
};

const unsupportedRow = {
  id: "mlx-community/Gemma-3-12B-Instruct-4bit",
  display_name: "Gemma 3 12B",
  load_status: "unsupported",
  runtime_compatibility: { supported: false },
};

describe("evaluateAnalysis", () => {
  it("is loading until the probes resolve", () => {
    const outcome = evaluateAnalysis({ analysis: null, endpoints: null });
    expect(outcome.status).toBe("loading");
    expect(outcome.recommendations).toHaveLength(0);
    expect(outcome.reason).toBeNull();
  });

  it("is unavailable with probe_failed when all endpoints fail", () => {
    const outcome = evaluateAnalysis({ analysis: null, endpoints: allFailed });
    expect(outcome.status).toBe("unavailable");
    expect(outcome.reason).toBe("probe_failed");
    expect(outcome.recommendations).toHaveLength(0);
  });

  it("is unavailable with probe_failed on partial failure with no model data", () => {
    // Only sysinfo succeeded; the model-bearing endpoints failed.
    const outcome = evaluateAnalysis({
      analysis: { setup: null, recommendations: null, models: null, sysinfo: {} },
      endpoints: { setup: false, recommendations: false, models: false, sysinfo: true },
    });
    expect(outcome.status).toBe("unavailable");
    expect(outcome.reason).toBe("probe_failed");
    expect(outcome.recommendations).toHaveLength(0);
  });

  it("is unavailable with no_supported_model when the catalog is empty", () => {
    const outcome = evaluateAnalysis({ analysis: analysisWithModels([]), endpoints: allOk });
    expect(outcome.status).toBe("unavailable");
    expect(outcome.reason).toBe("no_supported_model");
    expect(outcome.recommendations).toHaveLength(0);
  });

  it("is unavailable with no_supported_model on MLX-unsupported hardware", () => {
    const outcome = evaluateAnalysis({ analysis: analysisWithModels([unsupportedRow]), endpoints: allOk });
    expect(outcome.status).toBe("unavailable");
    expect(outcome.reason).toBe("no_supported_model");
    expect(outcome.recommendations).toHaveLength(0);
  });

  it("is ready and returns only supported models when a model can run", () => {
    const outcome = evaluateAnalysis({ analysis: analysisWithModels([supportedRow]), endpoints: allOk });
    expect(outcome.status).toBe("ready");
    expect(outcome.reason).toBeNull();
    expect(outcome.recommendations.length).toBeGreaterThan(0);
    expect(outcome.recommendations.every((model) => model.supported)).toBe(true);
  });

  it("never fabricates a supported model on any failure path", () => {
    const failurePaths = [
      evaluateAnalysis({ analysis: null, endpoints: allFailed }),
      evaluateAnalysis({ analysis: analysisWithModels([]), endpoints: allOk }),
      evaluateAnalysis({ analysis: analysisWithModels([unsupportedRow]), endpoints: allOk }),
    ];
    for (const outcome of failurePaths) {
      expect(outcome.recommendations.some((model) => model.supported)).toBe(false);
    }
  });

  it("treats a live /models endpoint as model data even when recommendations failed", () => {
    const outcome = evaluateAnalysis({
      analysis: { setup: null, recommendations: null, models: { recommended: [supportedRow], catalog: [], loaded: [] }, sysinfo: null },
      endpoints: { setup: false, recommendations: false, models: true, sysinfo: false },
    });
    expect(outcome.status).toBe("ready");
    expect(outcome.failedEndpoints).toEqual(["setup", "recommendations", "sysinfo"]);
  });
});

describe("buildRecommendations", () => {
  it("returns nothing at all for an absent analysis", () => {
    expect(buildRecommendations(null)).toEqual([]);
    expect(buildRecommendations({})).toEqual([]);
  });

  it("merges duplicate rows, honours the top pick and fills the three roles", () => {
    const analysis: FlowAnalysis = {
      recommendations: {
        recommendations: {
          models: [
            {
              id: "top-1",
              name: "Gemma 4 26B",
              reason: "custom reason",
              size: "8GB",
              download_required: true,
              source_url: "https://huggingface.co/mlx-community/gemma",
            },
          ],
          top_pick: { id: "top-1" },
        },
      },
      models: {
        recommended: [
          { id: "top-1", display_name: "Gemma 4 26B" },
          { model_id: "qwen-8b", name: "Qwen 8B", engine: "local_gguf", download_required: true, size: "2GB", provider: "ollama" },
          { recommended_load_id: "llama-70b", name: "Llama 70B", download_required: true, download_size: "40GB", repository: "https://example.com/models/llama" },
          { note: "row without any id is skipped" },
        ],
        catalog: [{ id: "plain", name: "Plain Model" }],
      },
    };
    const [best, faster, advanced] = buildRecommendations(analysis);
    expect(best).toMatchObject({ id: "top-1", role: "best", reason: "custom reason", engine: "local_mlx" });
    expect(faster).toMatchObject({ id: "qwen-8b", role: "faster", reason: "faster", engine: "local_gguf", externalHost: "ollama" });
    expect(advanced).toMatchObject({ id: "llama-70b", role: "advanced", reason: "advanced", externalHost: "example.com" });
    expect(best.externalHost).toBe("huggingface");
    expect(best.estimatedDownloadMinutes).toBe(73); // 8GB at ~15 Mbps
    expect(faster.downloadSize).toBe("2GB");
    expect(advanced.downloadSize).toBe("40GB");
    expect(best.parameterBillions).toBe(26);
    expect(faster.estimatedFirstResponseSeconds).toBe(5);
    expect(advanced.estimatedFirstResponseSeconds).toBe(30);
  });

  it("keeps a top pick that appears in no other list", () => {
    const analysis: FlowAnalysis = {
      recommendations: { recommendations: { top_pick: { id: "solo", name: "Solo 12B" } } },
    };
    const models = buildRecommendations(analysis);
    expect(models).toHaveLength(1);
    expect(models[0]).toMatchObject({ id: "solo", role: "best", parameterBillions: 12 });
  });

  it("falls back to the Gemma-12 name pattern and then to the first candidate", () => {
    const byName = buildRecommendations({
      models: { recommended: [{ id: "z", name: "Zeta" }, { id: "g12", name: "Gemma 12B" }] },
    });
    expect(byName[0].id).toBe("g12");
    expect(byName[1].id).toBe("z"); // second-choice fallback, no qwen/8b/7b match

    const byPosition = buildRecommendations({
      models: { recommended: [{ id: "alpha", name: "Alpha" }, { id: "beta", name: "Beta" }] },
    });
    expect(byPosition[0].id).toBe("alpha");
  });

  it("uses the generic third pick when nothing matches the advanced pattern", () => {
    const models = buildRecommendations({
      models: {
        recommended: [
          { id: "a", name: "Alpha" },
          { id: "b", name: "Beta" },
          { id: "c", name: "Gamma" },
        ],
      },
    });
    expect(models.map((model) => model.id)).toEqual(["a", "b", "c"]);
    expect(models[2].role).toBe("advanced");
  });

  it("still describes unsupported hardware honestly when nothing is supported", () => {
    const models = buildRecommendations({
      models: { recommended: [{ id: "u", name: "Unsupported 12B", load_status: "unsupported" }] },
    });
    expect(models).toHaveLength(1);
    expect(models[0].supported).toBe(false);
  });

  it("marks each unsupported signal and only full support as supported", () => {
    const rows = [
      { id: "s1", name: "Fine" },
      { id: "u1", name: "A", load_status: "unsupported" },
      { id: "u2", name: "B", load_status: "runtime_update_needed" },
      { id: "u3", name: "C", status: "not_recommended" },
      { id: "u4", name: "D", runtime_compatibility: { supported: false } },
    ];
    const supported = new Map(
      buildRecommendations({ models: { recommended: rows } }).map((model) => [model.id, model.supported]),
    );
    // The role picker returns three; check via toRecommendedModel for the rest.
    expect(supported.get("s1")).toBe(true);
    for (const row of rows.slice(1)) {
      expect(toRecommendedModel(row).supported).toBe(false);
    }
  });
});

describe("toRecommendedModel", () => {
  it("survives a row with nothing in it", () => {
    const model = toRecommendedModel({});
    expect(model.id).toBe("");
    expect(model.name).toBe("recommended_brain");
    expect(model.shortName).toBe("recommended brain");
    expect(model.family).toBe("recommended brain");
    expect(model.engine).toBe("local_mlx");
    expect(model.supported).toBe(true);
    expect(model.downloadRequired).toBe(false);
    expect(model.externalHost).toBe("");
    expect(model.estimatedDownloadMinutes).toBe(0);
    expect(model.estimatedFirstResponseSeconds).toBe(8);
    expect(model.parameterBillions).toBeNull();
    expect(model.storageLocation).toBe("~/.latticeai/models");
  });

  it("prefers each id, load id, engine and storage source in order", () => {
    expect(toRecommendedModel({ model_id: "m" }).id).toBe("m");
    expect(toRecommendedModel({ recommended_load_id: "r" }).id).toBe("r");
    expect(toRecommendedModel({ id: "x", load_id: "L" }).loadId).toBe("L");
    expect(toRecommendedModel({ id: "x" }).loadId).toBe("x");
    expect(toRecommendedModel({ id: "x", recommended_engine: "a", engine: "b" }).engine).toBe("a");
    expect(toRecommendedModel({ id: "x", engine: "b" }).engine).toBe("b");
    expect(toRecommendedModel({ id: "x", storage_location: "/models" }).storageLocation).toBe("/models");
    expect(toRecommendedModel({ id: "x", local_path: "/local" }).storageLocation).toBe("/local");
    expect(toRecommendedModel({ id: "x", family: "Fam" }).family).toBe("Fam");
    expect(toRecommendedModel({ id: "x", size: "3GB", reason: "why" })).toMatchObject({ size: "3GB", reason: "why" });
  });

  it("estimates an unknown download when a required download has no size", () => {
    const model = toRecommendedModel({ id: "x", download_required: true });
    expect(model.downloadRequired).toBe(true);
    expect(model.downloadSize).toBe("");
    expect(model.estimatedDownloadMinutes).toBeNull();
    // With no source fields at all, no external host can be claimed.
    expect(toRecommendedModel({ model_id: "m", download_required: true }).externalHost).toBe("");
    // A bare separator survives the protocol strip as itself.
    expect(toRecommendedModel({ id: "x", download_required: true, provider: "/" }).externalHost).toBe("/");
  });
});

describe("size and scale estimators", () => {
  it("parses download sizes across units and rejects nonsense", () => {
    expect(parseDownloadMegabytes("8GB")).toBe(8192);
    expect(parseDownloadMegabytes("512MB")).toBe(512);
    expect(parseDownloadMegabytes("1.5TB")).toBe(1.5 * 1024 * 1024);
    expect(parseDownloadMegabytes("8")).toBe(8192); // bare numbers assume gigabytes
    expect(parseDownloadMegabytes("")).toBeNull();
    expect(parseDownloadMegabytes("abc")).toBeNull();
    expect(parseDownloadMegabytes("0GB")).toBeNull();
    expect(parseDownloadMegabytes("..B")).toBeNull(); // dots match but do not parse
  });

  it("estimates minutes with a floor of one and null for unknown sizes", () => {
    expect(estimateDownloadMinutes("")).toBeNull();
    expect(estimateDownloadMinutes("1MB")).toBe(1);
    expect(estimateDownloadMinutes("8GB")).toBe(73);
  });

  it("reads parameter scale out of names and ignores zero or missing scales", () => {
    expect(parseParameterBillions("Gemma 12B")).toBe(12);
    expect(parseParameterBillions("qwen-8.5b")).toBe(8.5);
    expect(parseParameterBillions("no scale here")).toBeNull();
    expect(parseParameterBillions("0B")).toBeNull();
    expect(parseParameterBillions("")).toBeNull();
  });

  it("maps scale to a first-response estimate", () => {
    expect(estimateFirstResponseSeconds(null)).toBe(8);
    expect(estimateFirstResponseSeconds(9)).toBe(5);
    expect(estimateFirstResponseSeconds(16)).toBe(10);
    expect(estimateFirstResponseSeconds(40)).toBe(18);
    expect(estimateFirstResponseSeconds(41)).toBe(30);
  });
});

describe("naming helpers", () => {
  it("cleans package coordinates into names a person recognises", () => {
    expect(friendlyModelName("mlx-community/Qwen3-VL-8B-Instruct-4bit")).toBe("Qwen 3 8B");
    expect(friendlyModelName("Gemma-4-26B-A4B")).toBe("Gemma 4 26B");
    expect(friendlyModelName("Qwen3 Coder")).toBe("Qwen 3 Coder");
    expect(friendlyModelName("")).toBe("recommended brain");
  });

  it("asRecord accepts only plain objects", () => {
    expect(asRecord({ a: 1 })).toEqual({ a: 1 });
    expect(asRecord(null)).toEqual({});
    expect(asRecord([1, 2])).toEqual({});
    expect(asRecord("text")).toEqual({});
    expect(asRecord(undefined)).toEqual({});
  });

  it("fallbackModel names a supported local default", () => {
    const model = fallbackModel();
    expect(model.supported).toBe(true);
    expect(model.role).toBe("best");
    expect(model.loadId).toContain("Qwen3");
  });
});
