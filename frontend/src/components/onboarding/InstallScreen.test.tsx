import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { t } from "@/i18n";
import { ok, renderPage, type RenderPageOptions } from "@/test/renderPage";
import { InstallScreen, percentForStage } from "./InstallScreen";
import { fallbackModel, type RecommendedModel } from "./recommendationModel";

/**
 * The install step drives a real SSE prepare stream. These tests fake the
 * stream at the API boundary and check the honest parts: stages map to plain
 * words, percentages never invent progress, errors keep their recovery
 * guidance but lose their jargon, and the buttons freeze while work runs.
 */

function model(overrides: Partial<RecommendedModel> = {}): RecommendedModel {
  return {
    ...fallbackModel(),
    downloadRequired: true,
    downloadSize: "8GB",
    externalHost: "huggingface",
    estimatedDownloadMinutes: 25,
    ...overrides,
  };
}

type Handlers = {
  onProgress?: (data: Record<string, unknown>) => void;
  onDone?: (data: Record<string, unknown>) => void;
  onError?: (data: Record<string, unknown>) => void;
};

function renderInstall(
  subject: RecommendedModel,
  api: RenderPageOptions["api"] = {},
  options: Omit<RenderPageOptions, "api"> = {},
) {
  const callbacks = { onBack: vi.fn(), onComplete: vi.fn(), onLater: vi.fn() };
  renderPage(
    <InstallScreen model={subject} onBack={callbacks.onBack} onComplete={callbacks.onComplete} onLater={callbacks.onLater} />,
    { language: "ko", ...options, api },
  );
  return callbacks;
}

function stageItems() {
  return Array.from(document.querySelectorAll<HTMLElement>(".ritual-stage"));
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("InstallScreen", () => {
  it("lays out the plan for a download install before anything runs", () => {
    renderInstall(model());
    expect(screen.getByText(t("ko", "flow.install.title"))).toBeTruthy();
    expect(screen.getByText(t("ko", "flow.install.stage.idle"))).toBeTruthy();
    // Four stages, none active or done yet.
    expect(stageItems()).toHaveLength(4);
    for (const item of stageItems()) {
      expect(item.className).not.toMatch(/is-active|is-done|is-error/);
    }
    // Expected time folds in the download estimate.
    expect(
      screen.getByText(t("ko", "flow.install.expected", {
        download: t("ko", "flow.recommend.minutes", { count: 25 }),
        response: 5,
      })),
    ).toBeTruthy();
    expect(screen.getByText(t("ko", "flow.install.note"))).toBeTruthy();
    expect(screen.getByText(t("ko", "flow.install.local"))).toBeTruthy();
    expect(screen.getByRole("button", { name: t("ko", "flow.install.start") })).toBeTruthy();
  });

  it("describes an already-local model without any download talk", () => {
    renderInstall(model({ downloadRequired: false, downloadSize: "", externalHost: "", estimatedDownloadMinutes: 0 }));
    expect(screen.getByText(t("ko", "flow.install.title.ready"))).toBeTruthy();
    expect(screen.getByText(t("ko", "flow.install.step.ready"))).toBeTruthy();
    expect(screen.getByText(t("ko", "flow.install.expected.ready", { response: 5 }))).toBeTruthy();
    expect(screen.getByText(t("ko", "flow.install.timelineStep.ready"))).toBeTruthy();
    expect(screen.getByText(t("ko", "flow.install.note.ready"))).toBeTruthy();
    expect(screen.getByText(t("ko", "flow.install.local.ready"))).toBeTruthy();
    expect(screen.getByRole("button", { name: t("ko", "flow.install.startReady") })).toBeTruthy();
  });

  it("admits an unknown download time instead of estimating one", () => {
    renderInstall(model({ estimatedDownloadMinutes: null }));
    expect(screen.getByText(t("ko", "flow.install.expected.unknown", { response: 5 }))).toBeTruthy();
    // Unknown size means the timeline cannot promise a duration either.
    expect(screen.getAllByText(t("ko", "flow.install.timelineQuick")).length).toBeGreaterThanOrEqual(2);
  });

  it("treats a required download of size zero as ready to go", () => {
    renderInstall(model({ estimatedDownloadMinutes: 0 }));
    expect(screen.getByText(t("ko", "flow.install.expected.ready", { response: 5 }))).toBeTruthy();
  });

  it("refuses to start an unsupported model", () => {
    renderInstall(model({ supported: false }));
    const start = screen.getByRole("button", { name: t("ko", "flow.install.start") }) as HTMLButtonElement;
    expect(start.disabled).toBe(true);
  });

  it("walks the stages to done and enters the product after a breath", async () => {
    const prepare = vi.fn(async (body: Record<string, unknown>, handlers: Handlers) => {
      handlers.onProgress?.({}); // a frame with no stage still counts as installing
      handlers.onProgress?.({ stage: "download_weights", percent: 42, user_message: "custom raw words" });
      handlers.onProgress?.({ stage: "smoke_test", message: "verify pass" });
      handlers.onProgress?.({ stage: "load" });
      handlers.onProgress?.({ stage: "warmup" });
      handlers.onProgress?.({ stage: "complete" });
      handlers.onDone?.({ status: "done" });
      return ok({ status: "done" });
    });
    const callbacks = renderInstall(model({ engine: "" }), { streamModelPrepare: prepare });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: t("ko", "flow.install.start") }));
    });

    // The screen ends in the done state with the enter button showing.
    expect(screen.getByText(t("ko", "flow.install.stage.done"))).toBeTruthy();
    expect(document.querySelector(".ritual-bar-fill")?.className).toContain("progress-100");
    for (const item of stageItems()) expect(item.className).toContain("is-done");
    // An empty engine falls back to the local default.
    expect(prepare.mock.calls[0][0]).toMatchObject({ engine: "local_mlx", allow_download: true });

    await act(async () => {
      vi.advanceTimersByTime(700);
    });
    expect(callbacks.onComplete).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: t("ko", "flow.install.enter") }));
    expect(callbacks.onComplete).toHaveBeenCalledTimes(2);
  });

  it("shows live stage progress and the raw line only to advanced users", async () => {
    let handlers: Handlers = {};
    const prepare = vi.fn((body: Record<string, unknown>, incoming: Handlers) => {
      handlers = incoming;
      return new Promise(() => {});
    });
    renderInstall(model(), { streamModelPrepare: prepare }, { mode: "advanced" });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: t("ko", "flow.install.start") }));
    });
    // Busy: every button is frozen while work runs.
    expect(screen.getByRole("button", { name: t("ko", "flow.install.busy") })).toBeTruthy();
    expect((screen.getByRole("button", { name: t("ko", "flow.install.back") }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: t("ko", "flow.install.later") }) as HTMLButtonElement).disabled).toBe(true);

    await act(async () => {
      handlers.onProgress?.({ stage: "download_weights", user_message: "custom raw words" });
    });
    const download = stageItems()[1];
    expect(download.className).toContain("is-active");
    expect(stageItems()[0].className).toContain("is-done");
    // Percent fell back to the stage default (55) because the event named
    // none; the bar class rounds to the nearest ten.
    expect(document.querySelector(".ritual-bar-fill")?.className).toContain("progress-60");
    // The raw line shows because it differs from the friendly stage text.
    expect(screen.getByText("custom raw words")).toBeTruthy();

    // A raw message identical to the friendly text is not repeated.
    await act(async () => {
      handlers.onProgress?.({ stage: "download_weights", user_message: t("ko", "flow.install.stage.download") });
    });
    expect(document.querySelector(".ritual-status-raw")).toBeNull();
  });

  it("keeps the raw line away from everyday (basic) mode", async () => {
    let handlers: Handlers = {};
    const prepare = vi.fn((body: Record<string, unknown>, incoming: Handlers) => {
      handlers = incoming;
      return new Promise(() => {});
    });
    renderInstall(model(), { streamModelPrepare: prepare }, { mode: "basic" });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: t("ko", "flow.install.start") }));
    });
    await act(async () => {
      handlers.onProgress?.({ stage: "download_weights", user_message: "custom raw words" });
    });
    expect(document.querySelector(".ritual-status-raw")).toBeNull();
  });

  it("cleans jargon out of a stream error and keeps its recovery guidance", async () => {
    const prepare = vi.fn(async (body: Record<string, unknown>, handlers: Handlers) => {
      handlers.onError?.({
        user_message: "gemma4_unified failed because mlx-vlm runtime broke: No module named 'mlx_lm'",
        recovery_guidance: ["Install   the   runtime again", "", "Retry the download", "third hint is dropped"],
      });
      return { ok: false, status: 500, source: "live", data: { reason: "the huggingface pull was interrupted" } };
    });
    renderInstall(model(), { streamModelPrepare: prepare });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: t("ko", "flow.install.start") }));
    });

    const alert = screen.getByRole("alert");
    // The final message comes from the result payload, de-jargoned.
    expect(alert.textContent).toContain("the local model support pull was interrupted");
    expect(alert.textContent).toContain(t("ko", "flow.install.retry"));
    for (const item of stageItems()) expect(item.className).toContain("is-error");
    expect(screen.getByText(t("ko", "flow.install.stage.error"))).toBeTruthy();
  });

  it("falls back through error fields and a default when a failure says nothing", async () => {
    const prepare = vi.fn(async (body: Record<string, unknown>, handlers: Handlers) => {
      handlers.onError?.({ error: "boom" });
      return { ok: false, status: 500, source: "live", data: {} };
    });
    renderInstall(model(), { streamModelPrepare: prepare });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: t("ko", "flow.install.start") }));
    });
    expect(screen.getByRole("alert").textContent).toContain("The selected model could not be loaded.");
  });

  it("hands back and later to the caller while idle", () => {
    const callbacks = renderInstall(model());
    fireEvent.click(screen.getByRole("button", { name: t("ko", "flow.install.back") }));
    fireEvent.click(screen.getByRole("button", { name: t("ko", "flow.install.later") }));
    expect(callbacks.onBack).toHaveBeenCalledTimes(1);
    expect(callbacks.onLater).toHaveBeenCalledTimes(1);
  });

  it("maps every stage to a defensible percentage, including the unreachable ones", () => {
    expect(percentForStage("install")).toBe(20);
    expect(percentForStage("download")).toBe(55);
    expect(percentForStage("load")).toBe(82);
    expect(percentForStage("validate")).toBe(94);
    expect(percentForStage("done")).toBe(100);
    // The stream mapper never emits idle/error, but the fallback must stay sane.
    expect(percentForStage("idle")).toBe(8);
    expect(percentForStage("error")).toBe(8);
  });
});
