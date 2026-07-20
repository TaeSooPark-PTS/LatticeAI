import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { t } from "@/i18n";
import { useAppStore } from "@/store/appStore";
import { RecommendationScreen } from "./RecommendationScreen";
import type { RecommendedModel } from "./recommendationModel";

const supportedModel: RecommendedModel = {
  id: "mlx-community/Gemma-3-12B-Instruct-4bit",
  loadId: "mlx-community/Gemma-3-12B-Instruct-4bit",
  engine: "local_mlx",
  name: "Gemma 3 12B",
  shortName: "Gemma 3",
  family: "Gemma 3",
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
  parameterBillions: 12,
};

function renderScreen(overrides: Partial<React.ComponentProps<typeof RecommendationScreen>> = {}) {
  const props = {
    status: "unavailable" as const,
    reason: "probe_failed" as const,
    recommendations: [],
    analysis: null,
    onBack: vi.fn(),
    onRetry: vi.fn(),
    onSkipModel: vi.fn(),
    onSelect: vi.fn(),
    ...overrides,
  };
  render(<RecommendationScreen {...props} />);
  return props;
}

beforeEach(() => {
  useAppStore.setState({ language: "en" });
});

describe("RecommendationScreen", () => {
  it("never renders a supported fallback card when the probe failed", () => {
    renderScreen({ status: "unavailable", reason: "probe_failed", recommendations: [] });
    expect(screen.getByRole("alert")).toBeTruthy();
    // No fabricated model name and no primary CTA that implies readiness.
    expect(screen.queryByText(/Qwen/i)).toBeNull();
    expect(screen.queryByText(t("en", "flow.recommend.primary"))).toBeNull();
    expect(document.querySelector(".ritual-primary-model-button")).toBeNull();
    expect(document.querySelector(".ritual-model-card")).toBeNull();
  });

  it("shows retry and continue-without-model as the primary actions when unavailable", async () => {
    const props = renderScreen({ status: "unavailable", reason: "no_supported_model", recommendations: [] });
    const retry = screen.getByRole("button", { name: new RegExp(t("en", "flow.recommend.unavailable.retry")) });
    const skip = screen.getByRole("button", { name: t("en", "flow.recommend.skip") });
    await userEvent.click(retry);
    await userEvent.click(skip);
    expect(props.onRetry).toHaveBeenCalledTimes(1);
    expect(props.onSkipModel).toHaveBeenCalledTimes(1);
  });

  it("explains the no_supported_model cause without claiming readiness", () => {
    renderScreen({ status: "unavailable", reason: "no_supported_model", recommendations: [] });
    expect(screen.getByText(t("en", "flow.recommend.unavailable.empty"))).toBeTruthy();
  });

  it("does not render a model card while loading", () => {
    renderScreen({ status: "loading", reason: null, recommendations: [] });
    expect(document.querySelector(".ritual-model-card")).toBeNull();
    expect(document.querySelector(".ritual-primary-model-button")).toBeNull();
    expect(screen.getByText(t("en", "flow.recommend.loading"))).toBeTruthy();
  });

  it("renders the recommendation CTA only when a supported model is ready", () => {
    renderScreen({ status: "ready", reason: null, recommendations: [supportedModel] });
    expect(document.querySelector(".ritual-primary-model-button")).not.toBeNull();
    expect(screen.getByText("Gemma 3")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
