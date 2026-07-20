import { describe, expect, it } from "vitest";
import {
  evaluateAnalysis,
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
});
