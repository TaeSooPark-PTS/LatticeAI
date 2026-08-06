import { fireEvent, render, screen } from "@testing-library/react";
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

  it("shows download plus first-reply time for a model that must be fetched", () => {
    const downloadable: RecommendedModel = {
      ...supportedModel,
      downloadRequired: true,
      downloadSize: "8GB",
      size: "8GB",
      estimatedDownloadMinutes: 25,
    };
    renderScreen({ status: "ready", reason: null, recommendations: [downloadable] });
    const download = t("en", "flow.recommend.minutes", { count: 25 });
    expect(
      screen.getByText(t("en", "flow.recommend.timeEstimate", { download, response: 5 })),
    ).toBeTruthy();
    expect(
      screen.getByText(new RegExp(t("en", "flow.recommend.primaryNote", { time: download }))),
    ).toBeTruthy();
    expect(screen.getByText(new RegExp(t("en", "flow.recommend.nextHint")))).toBeTruthy();
    // A known size shows itself instead of the ready badge.
    expect(screen.getByText("8GB")).toBeTruthy();
  });

  it("admits an unknown download time on the hero card", () => {
    const unknownSize: RecommendedModel = {
      ...supportedModel,
      downloadRequired: true,
      downloadSize: "",
      estimatedDownloadMinutes: null,
    };
    renderScreen({ status: "ready", reason: null, recommendations: [unknownSize] });
    expect(
      screen.getByText(t("en", "flow.recommend.timeEstimate.unknown", { response: 5 })),
    ).toBeTruthy();
    expect(
      screen.getByText(new RegExp(t("en", "flow.recommend.primaryNote.unknown"))),
    ).toBeTruthy();
  });

  it("compares a faster or advanced hero against the default pick", () => {
    renderScreen({ status: "ready", reason: null, recommendations: [faster, supportedModel] });
    expect(screen.getByText(t("en", "flow.recommend.rank.faster"))).toBeTruthy();
    expect(screen.getByText(t("en", "flow.recommend.comparison.faster"))).toBeTruthy();
    // The compact alternative with no size wears the ready badge.
    expect(
      document.querySelector(".ritual-model-card.is-compact .ritual-alt-size")?.textContent,
    ).toBe(t("en", "flow.recommend.sizeReady"));
  });

  it("labels an advanced hero with its comparison", () => {
    renderScreen({
      status: "ready",
      reason: null,
      recommendations: [{ ...unsupported, supported: true }],
    });
    expect(screen.getByText(t("en", "flow.recommend.rank.advanced"))).toBeTruthy();
    expect(screen.getByText(t("en", "flow.recommend.comparison.advanced"))).toBeTruthy();
  });

  it("falls back to a numbered choice label for an unknown role", () => {
    // Runtime data is untyped: a role outside the known set must still label.
    renderScreen({
      status: "ready",
      reason: null,
      recommendations: [{ ...supportedModel, role: "choice" as RecommendedModel["role"] }],
    });
    expect(screen.getByText(t("en", "flow.recommend.rank.choice", { index: 1 }))).toBeTruthy();
  });

  it("warns instead of offering a start when the hero itself cannot run", () => {
    renderScreen({ status: "ready", reason: null, recommendations: [unsupported] });
    expect(screen.getByText(t("en", "flow.recommend.unsupported"))).toBeTruthy();
    expect(document.querySelector(".ritual-primary-model-button")).toBeNull();
  });

  it("keeps the guard even if a click slips through to a disabled alternative", () => {
    const props = renderScreen({
      status: "ready",
      reason: null,
      recommendations: [supportedModel, unsupported],
    });
    const card = screen.getByText("Llama 70B").closest("button") as HTMLButtonElement;
    // fireEvent dispatches even on disabled controls (assistive tech and
    // scripts can); the handler's own check must hold the line.
    fireEvent.click(card);
    expect(props.onSelect).not.toHaveBeenCalled();
  });

  it("starts the headline model from the hero card itself", async () => {
    const props = renderScreen({ status: "ready", reason: null, recommendations: [supportedModel, faster] });
    await userEvent.click(document.querySelector(".ritual-primary-model-button") as HTMLButtonElement);
    expect(props.onSelect).toHaveBeenCalledWith(supportedModel);
  });

  it("renders footer actions but no hero when ready arrives empty", () => {
    renderScreen({ status: "ready", reason: null, recommendations: [] });
    expect(document.querySelector(".ritual-primary-hero-card")).toBeNull();
    expect(document.querySelector(".ritual-alternatives-details")).toBeNull();
    expect(screen.getByRole("button", { name: t("en", "flow.recommend.back") })).toBeTruthy();
  });

  describe("environment banner", () => {
    it("keeps checking while the analysis has not landed", () => {
      renderScreen({ status: "ready", reason: null, recommendations: [supportedModel], analysis: null });
      expect(document.querySelector(".ritual-scan-banner.is-loading")).toBeTruthy();
    });

    it("confirms Apple Silicon with the flag and the reported RAM", () => {
      renderScreen({
        status: "ready",
        reason: null,
        recommendations: [supportedModel],
        analysis: { recommendations: { recommendations: { apple_silicon: true, ram_gb: 32 } } },
      });
      expect(document.querySelector(".ritual-scan-banner.is-success")?.textContent).toContain(
        t("en", "flow.recommend.environment.apple", { ram: 32 }),
      );
    });

    it("recognises Apple Silicon from the arch and RAM from megabytes", () => {
      renderScreen({
        status: "ready",
        reason: null,
        recommendations: [supportedModel],
        analysis: {
          setup: { environment: { arch: "arm64", ram_mb: 16384 } },
          recommendations: { profile: {} },
        },
      });
      expect(document.querySelector(".ritual-scan-banner.is-success")?.textContent).toContain(
        t("en", "flow.recommend.environment.apple", { ram: 16 }),
      );
    });

    it("marks other hardware as standard without inventing memory", () => {
      renderScreen({
        status: "ready",
        reason: null,
        recommendations: [supportedModel],
        analysis: { setup: { environment: { arch: "x86_64" } } },
      });
      expect(document.querySelector(".ritual-scan-banner.is-warning")?.textContent).toContain(
        t("en", "flow.recommend.environment.standard", { ram: 0 }),
      );
    });

    it("treats a probe that reported nothing as standard hardware", () => {
      renderScreen({ status: "ready", reason: null, recommendations: [supportedModel], analysis: {} });
      expect(document.querySelector(".ritual-scan-banner.is-warning")).toBeTruthy();
    });
  });
});
