import { act, fireEvent, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@/i18n/workspace";
import { ModelsPanel } from "../ModelsPanel";
import { ok, renderPage } from "@/test/renderPage";

describe("ModelsPanel download byte metrics and cooperative cancel", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("displays byte progress and cancels an active model preparation stream cleanly", async () => {
    let onProgress: ((event: Record<string, unknown>) => void) | undefined;
    let resolveStream: (res: unknown) => void = () => {};
    const unloadModel = vi.fn().mockResolvedValue({ ok: true, status: 200, source: "live", data: {} });
    const streamModelPrepare = vi.fn(
      (_body: Record<string, unknown>, handlers: { onProgress?: (e: Record<string, unknown>) => void; signal?: AbortSignal }) => {
        onProgress = handlers.onProgress;
        return new Promise((resolve) => {
          resolveStream = resolve;
        });
      },
    );

    renderPage(<ModelsPanel />, {
      mode: "advanced",
      language: "en",
      api: {
        models: ok({
          catalog: [
            {
              id: "mlx-community/qwen-test",
              name: "Qwen Test",
              download_required: true,
              load_available: true,
              recommended_engine: "local_mlx",
            },
          ],
          loaded: [],
          current: null,
        }),
        modelRecommendations: ok({ recommendations: { models: [] } }),
        embeddingsStatus: ok({ state: "production" }),
        streamModelPrepare,
        unloadModel,
      },
    });

    const checkbox = await screen.findByRole("checkbox");
    fireEvent.click(checkbox);

    const downloadBtn = await screen.findByRole("button", { name: "Install & use" });
    await act(async () => {
      fireEvent.click(downloadBtn);
    });

    await act(async () => {
      onProgress?.({
        stage: "download",
        percent: 35,
        message: "Downloading weights",
        downloaded_bytes: 512 * 1024 * 1024,
        total_bytes: 1024 * 1024 * 1024,
      });
    });

    expect(screen.getByTestId("library-progress-bytes").textContent).toBe("512 MB / 1.0 GB");

    await act(async () => {
      fireEvent.click(screen.getByTestId("library-cancel-prepare"));
      resolveStream({ ok: false, status: 0, source: "unavailable", data: {} });
    });

    expect(unloadModel).toHaveBeenCalledWith("mlx-community/qwen-test");
    expect(screen.getByTestId("library-download-cancelled").textContent).toContain("Model preparation cancelled.");
  });
});
