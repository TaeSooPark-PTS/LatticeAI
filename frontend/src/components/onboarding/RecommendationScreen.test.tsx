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

const faster: RecommendedModel = {
  ...supportedModel,
  id: "mlx-community/Qwen3-4B-4bit",
  loadId: "mlx-community/Qwen3-4B-4bit",
  name: "Qwen 3 4B",
  shortName: "Qwen 3",
  family: "Qwen 3",
  size: "2.4GB",
  role: "faster",
  reason: "quicker replies on a smaller machine",
  parameterBillions: 4,
};

const unsupported: RecommendedModel = {
  ...supportedModel,
  id: "mlx-community/Llama-3-70B-4bit",
  loadId: "mlx-community/Llama-3-70B-4bit",
  name: "Llama 3 70B",
  shortName: "Llama 70B",
  family: "Llama 3",
  size: "40GB",
  role: "advanced",
  reason: "needs more memory than this machine has",
  supported: false,
  parameterBillions: 70,
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

  it("names the top recommendation once instead of as a CTA above its own card", () => {
    // The screen used to render items[0] twice: a bare CTA button at the top,
    // then the same model again as the first card of the list below it, with
    // nothing saying which to press. One hero card now owns both.
    renderScreen({ status: "ready", reason: null, recommendations: [supportedModel, faster, unsupported] });
    expect(screen.getAllByText("Gemma 3")).toHaveLength(1);
    expect(document.querySelectorAll(".ritual-primary-model-button")).toHaveLength(1);
    // The hero is not one of the compact alternative cards.
    expect(document.querySelectorAll(".ritual-model-card.is-compact")).toHaveLength(2);
    expect(document.querySelector(".ritual-model-card.is-compact")?.textContent).not.toContain("Gemma 3");
  });

  it("labels the hero card by the model it recommends", () => {
    renderScreen({ status: "ready", reason: null, recommendations: [supportedModel, faster] });
    expect(screen.getByRole("heading", { level: 2, name: "Gemma 3" })).toBeTruthy();
    expect(
      screen.getByRole("heading", { level: 2, name: t("en", "flow.recommend.alternatives") }),
    ).toBeTruthy();
  });

  it("picks the alternative that was clicked, not the headline model", async () => {
    const props = renderScreen({
      status: "ready",
      reason: null,
      recommendations: [supportedModel, faster],
    });
    await userEvent.click(screen.getByText("Qwen 3").closest("button")!);
    expect(props.onSelect).toHaveBeenCalledTimes(1);
    expect(props.onSelect).toHaveBeenCalledWith(faster);
  });

  it("disables an alternative this machine cannot run", async () => {
    const props = renderScreen({
      status: "ready",
      reason: null,
      recommendations: [supportedModel, unsupported],
    });
    const card = screen.getByText("Llama 70B").closest("button") as HTMLButtonElement;
    expect(card.disabled).toBe(true);
    await userEvent.click(card);
    expect(props.onSelect).not.toHaveBeenCalled();
  });

  it("keeps back and skip reachable once a recommendation is showing", async () => {
    const props = renderScreen({ status: "ready", reason: null, recommendations: [supportedModel] });
    await userEvent.click(screen.getByRole("button", { name: t("en", "flow.recommend.back") }));
    await userEvent.click(screen.getByRole("button", { name: t("en", "flow.recommend.skip") }));
    expect(props.onBack).toHaveBeenCalledTimes(1);
    expect(props.onSkipModel).toHaveBeenCalledTimes(1);
  });

  it("renders no alternatives block when there is only one recommendation", () => {
    renderScreen({ status: "ready", reason: null, recommendations: [supportedModel] });
    expect(document.querySelector(".ritual-alternatives")).toBeNull();
    expect(screen.queryByText(t("en", "flow.recommend.alternatives"))).toBeNull();
  });
});
